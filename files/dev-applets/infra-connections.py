#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""
infra-connections — Tailscale + ZeroTier status window + KDE tray indicator

A system-tray icon that opens a Breeze Dark window:
  • Tailscale connection state, IP, hostname, peer count
  • ZeroTier connection state, joined networks + IPs
  • Live colour-coded check log

Tray icon colour:
  green  = both VPNs connected
  yellow = only one connected
  red    = neither connected

Left-click or double-click tray icon → open/raise window.
Right-click → context menu (check now, quit).

Requirements:
    python3-pyqt6   (apt install python3-pyqt6)
    tailscale       (optional — shown as "not installed" if absent)
    zerotier-one    (optional — shown as "not installed" if absent)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QPushButton, QScrollArea, QSystemTrayIcon,
    QTextEdit, QVBoxLayout, QWidget,
)

WIN_W, WIN_H = 600, 500
CHECK_INTERVAL_MS = 60 * 60 * 1000   # re-check every hour

# ── Config file (NOT tracked by dotfile-sync) ─────────────────────────────────
# ~/.config/infra-connections/config.ini
# Add ZeroTier peer IPs here to verify actual connectivity, not just daemon state.
INFRA_CONFIG = Path.home() / ".config" / "infra-connections" / "config.ini"

_SAMPLE_CONFIG = """\
[zerotier]
# Comma-separated IPs to ping inside your ZeroTier network(s).
# At least one must respond for status to show as ✓ connected.
# Example (your actual managed IPs — do not commit to git if private):
# ping_hosts = 172.27.0.1, 172.27.0.5
ping_hosts =

[tailscale]
# Optional: IPs to ping to verify actual Tailscale routing.
# If empty, daemon status alone determines connected state.
ping_hosts =
"""

# ── Stylesheet (Breeze Dark — matches dotfile-sync / setup-installer) ────────
QSS = """
QMainWindow, QWidget#root { background: #232629; }
QWidget#titlebar  { background: #1b1e20; border-bottom: 1px solid #3a3f44; }
QFrame#divider    { background: #3a3f44; max-height: 1px; }
QFrame#divider_v  { background: #3a3f44; max-width:  1px; }

QScrollArea           { background: transparent; border: none; }
QScrollBar:vertical   { background: #1b1e20; width: 8px; }
QScrollBar::handle:vertical { background: #4a5056; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QLabel#title    { color: #eff0f1; font-size: 15px; font-weight: bold; }
QLabel#section  { color: #3daee9; font-size: 12px; font-weight: bold; }
QLabel#body     { color: #bdc3c7; font-size: 12px; }
QLabel#caption  { color: #4a5056; font-size: 10px; }

QLabel#status_ok   { color: #27ae60; font-size: 22px; }
QLabel#status_spin { color: #f1c40f; font-size: 22px; }
QLabel#status_err  { color: #da4453; font-size: 22px; }
QLabel#status_text { color: #eff0f1; font-size: 13px; font-weight: bold; }
QLabel#status_sub  { color: #7f8c8d; font-size: 11px; }

QLabel#svc_name    { color: #eff0f1; font-size: 12px; font-weight: bold; }
QLabel#svc_ip      { color: #3daee9; font-size: 11px; }
QLabel#svc_detail  { color: #7f8c8d; font-size: 10px; }
QLabel#svc_ok      { color: #27ae60; font-size: 15px; }
QLabel#svc_err     { color: #da4453; font-size: 15px; }
QLabel#svc_pending { color: #4a5056; font-size: 15px; }

QTextEdit#log {
    background: #1b1e20; color: #bdc3c7;
    border: 1px solid #3a3f44;
    font-family: "JetBrains Mono","Fira Code","Courier New",monospace;
    font-size: 10px;
}

QPushButton#primary {
    background: #3daee9; color: #1b1e20;
    border: none; border-radius: 3px;
    padding: 8px 20px; font-size: 12px; font-weight: bold;
}
QPushButton#primary:hover    { background: #56b9ec; }
QPushButton#primary:pressed  { background: #2b9dc8; }
QPushButton#primary:disabled { background: #3a3f44; color: #4a5056; }

QPushButton#secondary {
    background: transparent; color: #3daee9;
    border: 1px solid #3a3f44; border-radius: 3px;
    padding: 7px 16px; font-size: 12px;
}
QPushButton#secondary:hover    { background: #2d3136; border-color: #3daee9; }
QPushButton#secondary:pressed  { background: #1e3040; }
QPushButton#secondary:disabled { color: #4a5056; border-color: #3a3f44; }

QPushButton#tb_close {
    background: transparent; color: #7f8c8d; border: none;
    font-size: 14px; padding: 0 4px;
}
QPushButton#tb_close:hover { color: #da4453; }
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _divider() -> QFrame:
    f = QFrame(); f.setObjectName("divider"); f.setFixedHeight(1); return f

def _vdivider() -> QFrame:
    f = QFrame(); f.setObjectName("divider_v"); f.setFixedWidth(1); return f

def _tray_icon(colour: str) -> QIcon:
    pix = QPixmap(22, 22)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(colour))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(3, 3, 16, 16)
    p.end()
    return QIcon(pix)

def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    """Run a command; return (returncode, combined stdout+stderr)."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return -1, "__not_installed__"
    except subprocess.TimeoutExpired:
        return -2, "timed out"
    except Exception as exc:
        return -3, str(exc)

def _ping(ip: str, timeout: int = 2) -> bool:
    """Return True if the host responds to a single ICMP ping."""
    rc, _ = _run(["ping", "-c", "1", "-W", str(timeout), ip.strip()], timeout=timeout + 2)
    return rc == 0

def _load_ping_targets(section: str) -> list[str]:
    """Read ping_hosts from ~/.config/infra-connections/config.ini for a section."""
    if not INFRA_CONFIG.exists():
        return []
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(INFRA_CONFIG)
    raw = cfg.get(section, "ping_hosts", fallback="").strip()
    return [h.strip() for h in raw.split(",") if h.strip()]

def _ensure_config() -> None:
    """Create a sample config if none exists yet."""
    if not INFRA_CONFIG.exists():
        INFRA_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        INFRA_CONFIG.write_text(_SAMPLE_CONFIG)


# ── Service status data ───────────────────────────────────────────────────────

@dataclass
class ServiceStatus:
    name:      str
    connected: bool
    state:     str              # short human state string
    ips:       list[str] = field(default_factory=list)
    extra:     list[str] = field(default_factory=list)  # detail rows in left panel


# ── Background worker ─────────────────────────────────────────────────────────

class CheckWorker(QThread):
    log_line = pyqtSignal(str)
    finished = pyqtSignal(object, object)   # (ServiceStatus, ServiceStatus)

    def run(self) -> None:
        _ensure_config()
        ts = self._check_tailscale()
        zt = self._check_zerotier()
        self.finished.emit(ts, zt)

    # ── Tailscale ─────────────────────────────────────────────────────────────

    def _check_tailscale(self) -> ServiceStatus:
        self.log_line.emit("Checking Tailscale…")
        rc, out = _run(["tailscale", "status", "--json"])

        if rc == -1:
            self.log_line.emit("  Tailscale: not installed")
            return ServiceStatus("Tailscale", False, "not installed")

        if rc != 0 or not out:
            self.log_line.emit(f"  Tailscale: error (exit {rc})")
            return ServiceStatus("Tailscale", False, f"error (exit {rc})")

        try:
            data       = json.loads(out)
            state      = data.get("BackendState", "unknown")
            self_node  = data.get("Self") or {}
            ips        = self_node.get("TailscaleIPs", [])
            hostname   = self_node.get("HostName", "")
            dns        = self_node.get("DNSName", "").rstrip(".")
            peers      = len(data.get("Peer") or {})
            connected  = state == "Running"

            extra = []
            if hostname:
                extra.append(f"Host:  {hostname}")
            if dns:
                extra.append(f"DNS:   {dns}")
            extra.append(f"Peers: {peers}")

            ip_str = ips[0] if ips else "no IP"
            self.log_line.emit(f"  Tailscale: {state}  {ip_str}")

            # Optional ping verification
            ping_targets = _load_ping_targets("tailscale")
            if connected and ping_targets:
                reached = False
                for host in ping_targets:
                    self.log_line.emit(f"  Tailscale ping → {host} …")
                    if _ping(host):
                        self.log_line.emit(f"  Tailscale ping → {host}  ✓")
                        reached = True
                        break
                    else:
                        self.log_line.emit(f"  Tailscale ping → {host}  ✗ no reply")
                if not reached:
                    connected = False
                    state = "no peers reachable"
                    extra.append("⚠ ping targets unreachable")

            return ServiceStatus("Tailscale", connected, state, ips[:2], extra)

        except (json.JSONDecodeError, KeyError) as exc:
            self.log_line.emit(f"  Tailscale: parse error — {exc}")
            return ServiceStatus("Tailscale", False, "parse error")

    # ── ZeroTier ──────────────────────────────────────────────────────────────

    def _check_zerotier(self) -> ServiceStatus:
        self.log_line.emit("Checking ZeroTier…")

        # zerotier-cli requires root — use the local HTTP API instead.
        # Auth token is at /var/lib/zerotier-one/authtoken.secret (root-only)
        # or ~/.config/zerotier-one/authtoken.secret (user copy, if present).
        token = self._zt_token()
        if token is None:
            # Fall back to sudo zerotier-cli as last resort
            rc, out = _run(["sudo", "-n", "zerotier-cli", "status"])
            if rc == -1 or (rc != 0 and "not installed" in out.lower()):
                self.log_line.emit("  ZeroTier: not installed")
                return ServiceStatus("ZeroTier", False, "not installed")
            if rc == 0:
                online = "ONLINE" in out.upper()
                return ServiceStatus("ZeroTier", online, "ONLINE" if online else "offline")
            # sudo failed — detect connected state via zt* network interfaces
            iface_status = self._check_zerotier_via_iface()
            if iface_status is not None:
                return iface_status
            self.log_line.emit("  ZeroTier: no auth token + sudo failed — run: sudo zerotier-cli status")
            return ServiceStatus("ZeroTier", False, "no auth (run as root)")

        # Query local REST API (no root needed)
        try:
            import urllib.request as _ur
            def _zt_get(path: str):
                req = _ur.Request(
                    f"http://127.0.0.1:9993{path}",
                    headers={"X-ZT1-Auth": token},
                )
                with _ur.urlopen(req, timeout=5) as r:
                    return json.loads(r.read())

            info   = _zt_get("/status")
            online = info.get("online", False)
            node_id = info.get("address", "")
            state  = "ONLINE" if online else "OFFLINE"

            nets_data = _zt_get("/network")
            networks: list[str] = []
            ips: list[str] = []
            for net in nets_data:
                name   = net.get("name", "") or net.get("id", "")[:10]
                status = net.get("status", "")
                assigned = net.get("assignedAddresses", [])
                networks.append(f"{name}  [{status}]")
                ips.extend(a.split("/")[0] for a in assigned)

            extra = [f"Node: {node_id}"] + networks[:3] if node_id else networks[:4]
            if not networks:
                extra.append("No networks joined")

            # Ping verification — config targets first, then discovered peer IPs
            ping_targets = _load_ping_targets("zerotier")
            if online and ping_targets:
                reached = False
                for host in ping_targets:
                    self.log_line.emit(f"  ZeroTier ping → {host} …")
                    if _ping(host):
                        self.log_line.emit(f"  ZeroTier ping → {host}  ✓")
                        reached = True
                        break
                    else:
                        self.log_line.emit(f"  ZeroTier ping → {host}  ✗ no reply")
                if not reached:
                    online = False
                    state  = "no peers reachable"
                    extra.append("⚠ ping targets unreachable")
            elif online and not ping_targets:
                self.log_line.emit("  ZeroTier: no ping targets configured — daemon state only")
                self.log_line.emit(f"  → Edit {INFRA_CONFIG} to add ping_hosts for real verification")

            self.log_line.emit(f"  ZeroTier: {state}  networks={len(networks)}")
            return ServiceStatus("ZeroTier", online, state, ips[:2], extra)

        except Exception as exc:
            self.log_line.emit(f"  ZeroTier: API error — {exc}")
            return ServiceStatus("ZeroTier", False, f"API error: {exc}")

    def _check_zerotier_via_iface(self) -> "ServiceStatus | None":
        """Detect ZeroTier by scanning for zt* network interfaces (no root needed)."""
        rc, out = _run(["ip", "-br", "addr", "show"])
        if rc != 0:
            return None
        ips: list[str] = []
        ifaces: list[str] = []
        for line in out.splitlines():
            parts = line.split()
            if not parts or not parts[0].startswith("zt"):
                continue
            iface = parts[0]
            state = parts[1] if len(parts) > 1 else ""
            ifaces.append(iface)
            for addr in parts[2:]:
                ip = addr.split("/")[0]
                if ":" not in ip:
                    ips.append(ip)
        if not ifaces:
            return None
        self.log_line.emit(
            f"  ZeroTier: interface {ifaces[0]} detected  {ips[0] if ips else 'no IP'}"
            "  (auth token unreadable — interface check only)"
        )
        extra = [f"Interface: {ifaces[0]}",
                 "Auth token unreadable — daemon API skipped",
                 "Install authtoken: sudo cp /var/lib/zerotier-one/authtoken.secret"
                 " ~/.config/zerotier-one/ && chmod 600 ~/.config/zerotier-one/authtoken.secret"]
        return ServiceStatus("ZeroTier", True, "ONLINE", ips[:2], extra)

    @staticmethod
    def _zt_token() -> str | None:
        """Read ZeroTier auth token — user copy first, then system (needs root)."""
        candidates = [
            Path.home() / ".config" / "zerotier-one" / "authtoken.secret",
            Path("/var/lib/zerotier-one/authtoken.secret"),
        ]
        for p in candidates:
            try:
                return p.read_text().strip()
            except (PermissionError, FileNotFoundError):
                continue
        return None


# ── Custom title bar ──────────────────────────────────────────────────────────

class TitleBar(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("titlebar")
        self.setFixedHeight(36)
        self._drag: QPoint | None = None

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 8, 0)
        h.setSpacing(8)

        lbl = QLabel(title)
        lbl.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        h.addWidget(lbl)
        h.addStretch()

        gh = QLabel('<a href="https://github.com/BeanGreen247/kubuntu-setup" style="color:#4a5056;text-decoration:none;font-size:10px;">github</a>')
        gh.setOpenExternalLinks(False)
        gh.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        h.addWidget(gh)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("tb_close")
        close_btn.clicked.connect(lambda: self.window().hide())
        h.addWidget(close_btn)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag = ev.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, ev):
        if self._drag and ev.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(ev.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, ev):
        self._drag = None


# ── Main window ───────────────────────────────────────────────────────────────

class InfraWindow(QMainWindow):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app    = app
        self._worker: CheckWorker | None = None
        self._last_check = "never"

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
        )
        self.setFixedSize(WIN_W, WIN_H)
        self.setWindowTitle("Infra Connections")

        root = QWidget(); root.setObjectName("root")
        self.setCentralWidget(root)

        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(TitleBar("Infra Connections"))
        v.addWidget(self._build_body(), 1)
        v.addWidget(_divider())
        v.addWidget(self._build_footer())

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_body(self) -> QWidget:
        w = QWidget(); w.setObjectName("root")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_left())
        h.addWidget(_vdivider())
        h.addWidget(self._build_right(), 1)
        return w

    def _build_left(self) -> QWidget:
        panel = QWidget(); panel.setObjectName("root")
        panel.setFixedWidth(230)
        v = QVBoxLayout(panel)
        v.setContentsMargins(20, 20, 16, 16)
        v.setSpacing(0)

        # ── Overall status ────────────────────────────────────────────────────
        hrow = QHBoxLayout()
        self._status_icon = QLabel("□")
        self._status_icon.setObjectName("status_spin")
        self._status_icon.setFixedWidth(32)

        scol = QVBoxLayout(); scol.setSpacing(0)
        self._status_text = QLabel("Not checked")
        self._status_text.setObjectName("status_text")
        self._status_sub  = QLabel("—")
        self._status_sub.setObjectName("status_sub")
        scol.addWidget(self._status_text)
        scol.addWidget(self._status_sub)

        hrow.addWidget(self._status_icon)
        hrow.addSpacing(8)
        hrow.addLayout(scol, 1)
        v.addLayout(hrow)

        v.addSpacing(16)
        v.addWidget(_divider())
        v.addSpacing(14)

        # ── Tailscale block ───────────────────────────────────────────────────
        self._ts_block = self._make_service_block("Tailscale")
        v.addLayout(self._ts_block["layout"])

        v.addSpacing(14)
        v.addWidget(_divider())
        v.addSpacing(14)

        # ── ZeroTier block ────────────────────────────────────────────────────
        self._zt_block = self._make_service_block("ZeroTier")
        v.addLayout(self._zt_block["layout"])

        v.addStretch()
        return panel

    def _make_service_block(self, name: str) -> dict:
        layout = QVBoxLayout(); layout.setSpacing(4)

        hdr = QHBoxLayout()
        icon = QLabel("□"); icon.setObjectName("svc_pending"); icon.setFixedWidth(20)
        lbl  = QLabel(name); lbl.setObjectName("section")
        hdr.addWidget(icon); hdr.addSpacing(4); hdr.addWidget(lbl, 1)
        layout.addLayout(hdr)

        ip_lbl = QLabel("—"); ip_lbl.setObjectName("svc_ip"); ip_lbl.setWordWrap(True)
        layout.addWidget(ip_lbl)

        detail_lbl = QLabel("—"); detail_lbl.setObjectName("svc_detail"); detail_lbl.setWordWrap(True)
        layout.addWidget(detail_lbl)

        return {"layout": layout, "icon": icon, "ip": ip_lbl, "detail": detail_lbl}

    def _build_right(self) -> QWidget:
        panel = QWidget(); panel.setObjectName("root")
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 20, 20, 16)
        v.setSpacing(8)

        sec = QLabel("Connection log"); sec.setObjectName("section")
        v.addWidget(sec)

        self._log = QTextEdit(); self._log.setObjectName("log"); self._log.setReadOnly(True)
        v.addWidget(self._log, 1)
        return panel

    def _build_footer(self) -> QWidget:
        w = QWidget(); w.setObjectName("root")
        h = QHBoxLayout(w)
        h.setContentsMargins(20, 10, 20, 14)
        h.setSpacing(10)

        self._btn_check = QPushButton("Check Now")
        self._btn_check.setObjectName("primary")
        self._btn_check.clicked.connect(self.run_check)

        self._last_check_lbl = QLabel("Checked: never")
        self._last_check_lbl.setObjectName("caption")

        btn_close = QPushButton("Close")
        btn_close.setObjectName("secondary")
        btn_close.clicked.connect(self.hide)

        h.addWidget(self._btn_check)
        h.addStretch()
        h.addWidget(self._last_check_lbl)
        h.addWidget(btn_close)
        return w

    # ── Check logic ───────────────────────────────────────────────────────────

    def run_check(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._btn_check.setEnabled(False)
        self._status_icon.setText("□")
        self._status_icon.setObjectName("status_spin")
        self._status_text.setText("Checking…")
        self._status_sub.setText("—")
        self._status_icon.setStyleSheet(""); self._status_icon.style().polish(self._status_icon)
        self._append_log("── Checking Tailscale + ZeroTier ──")

        self._worker = CheckWorker()
        self._worker.log_line.connect(self._append_log)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_finished(self, ts: object, zt: object) -> None:
        ts_s: ServiceStatus = ts   # type: ignore[assignment]
        zt_s: ServiceStatus = zt   # type: ignore[assignment]

        self._last_check = datetime.now().strftime("%H:%M")
        self._last_check_lbl.setText(f"Checked: {self._last_check}")
        self._btn_check.setEnabled(True)

        both   = ts_s.connected and zt_s.connected
        either = ts_s.connected or  zt_s.connected

        if both:
            self._status_icon.setText("✓")
            self._status_icon.setObjectName("status_ok")
            self._status_text.setText("All connected")
            self._status_sub.setText("Tailscale + ZeroTier up")
        elif either:
            who = "Tailscale" if ts_s.connected else "ZeroTier"
            self._status_icon.setText("□")
            self._status_icon.setObjectName("status_spin")
            self._status_text.setText("Partial")
            self._status_sub.setText(f"Only {who} connected")
        else:
            self._status_icon.setText("✗")
            self._status_icon.setObjectName("status_err")
            self._status_text.setText("Disconnected")
            self._status_sub.setText("No VPN active")

        self._status_icon.setStyleSheet(""); self._status_icon.style().polish(self._status_icon)
        self._refresh_service_block(self._ts_block, ts_s)
        self._refresh_service_block(self._zt_block, zt_s)
        self._append_log(f"── Done: ts={ts_s.state}  zt={zt_s.state} ──")

    def _refresh_service_block(self, block: dict, s: ServiceStatus) -> None:
        if s.connected:
            block["icon"].setText("✓"); block["icon"].setObjectName("svc_ok")
        elif s.state == "not installed":
            block["icon"].setText("—"); block["icon"].setObjectName("svc_pending")
        else:
            block["icon"].setText("✗"); block["icon"].setObjectName("svc_err")
        block["icon"].setStyleSheet(""); block["icon"].style().polish(block["icon"])
        block["ip"].setText("  ".join(s.ips) if s.ips else s.state)
        block["detail"].setText("\n".join(s.extra) if s.extra else "")

    # ── Log ───────────────────────────────────────────────────────────────────

    def _append_log(self, line: str) -> None:
        if not line.strip():
            return
        low = line.lower()
        if "✓" in line or "online" in low or "running" in low:
            colour = "#27ae60"
        elif "✗" in line or "error" in low or "fail" in low or "not running" in low:
            colour = "#da4453"
        elif "──" in line:
            colour = "#3daee9"
        elif "not installed" in low:
            colour = "#f1c40f"
        else:
            colour = "#bdc3c7"

        cur = self._log.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        fmt = cur.charFormat()
        fmt.setForeground(QColor(colour))
        cur.setCharFormat(fmt)
        ts = datetime.now().strftime("%H:%M:%S")
        cur.insertText(f"[{ts}] {line}\n")
        self._log.setTextCursor(cur)
        self._log.ensureCursorVisible()


# ── System tray wrapper ───────────────────────────────────────────────────────

class InfraTray:
    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._win = InfraWindow(app)

        self._tray = QSystemTrayIcon(_tray_icon("#f1c40f"))
        self._tray.setToolTip("Infra Connections — checking…")
        self._tray.setContextMenu(self._build_menu())
        self._tray.activated.connect(self._on_tray_activated)
        self._attempt_show_tray()

        # Wrap _on_finished to also drive the tray icon colour
        _orig = self._win._on_finished

        def _patched(ts: object, zt: object) -> None:
            _orig(ts, zt)
            ts_s: ServiceStatus = ts   # type: ignore[assignment]
            zt_s: ServiceStatus = zt   # type: ignore[assignment]
            both   = ts_s.connected and zt_s.connected
            either = ts_s.connected or  zt_s.connected
            if both:
                self._tray.setIcon(_tray_icon("#27ae60"))
                self._tray.setToolTip("Infra: Tailscale + ZeroTier connected")
            elif either:
                who = "Tailscale" if ts_s.connected else "ZeroTier"
                self._tray.setIcon(_tray_icon("#f1c40f"))
                self._tray.setToolTip(f"Infra: only {who} connected")
            else:
                self._tray.setIcon(_tray_icon("#da4453"))
                self._tray.setToolTip("Infra: no VPN active")

        self._win._on_finished = _patched  # type: ignore[method-assign]

        # First check 10 s after startup; repeat hourly
        QTimer.singleShot(10_000, self._win.run_check)
        self._timer = QTimer()
        self._timer.setInterval(CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self._win.run_check)
        self._timer.start()

    def _attempt_show_tray(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()
        else:
            QTimer.singleShot(1_000, self._attempt_show_tray)

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        act_open = menu.addAction(QIcon.fromTheme("network-vpn"), "Open status window")
        act_open.triggered.connect(self._show_window)
        menu.addSeparator()
        act_check = menu.addAction(QIcon.fromTheme("view-refresh"), "Check now")
        act_check.triggered.connect(self._win.run_check)
        menu.addSeparator()
        act_quit = menu.addAction(QIcon.fromTheme("application-exit"), "Quit")
        act_quit.triggered.connect(self._app.quit)
        return menu

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_window()

    def _show_window(self) -> None:
        if self._win.isVisible():
            self._win.raise_()
            self._win.activateWindow()
        else:
            geo = self._app.primaryScreen().availableGeometry()
            self._win.move(
                (geo.width()  - WIN_W) // 2,
                (geo.height() - WIN_H) // 2,
            )
            self._win.show()
            self._win.raise_()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("infra-connections")
    app.setDesktopFileName("infra-connections")
    app.setStyleSheet(QSS)

    _tray = InfraTray(app)  # noqa: F841
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
