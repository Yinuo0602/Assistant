import sys
import os
import threading
import time
import json
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QLineEdit, QPushButton, QTextEdit, 
    QGroupBox, QCheckBox, QComboBox, QMessageBox,
    QTabWidget, QPlainTextEdit, QSplitter, QScrollArea
)
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtCore import Qt, Signal, QObject, Slot

from src.utils.logger import get_logger
from src.utils.paths import get_resource_path, get_config_path
from src.core.browser import BrowserService, BrowserCommand
from src.core.controller import LiveController
from src.core.event_bus import EventBus, EventType
from src.core.asr import VoiceListener
from src.core.executor import DouyinExecutor

logger = get_logger()

# 用于将日志重定向到 GUI 的信号类
class LogSignal(QObject):
    log_received = Signal(str)

class DanmuSignal(QObject):
    danmu_received = Signal(str)

log_signal = LogSignal()
danmu_signal = DanmuSignal()

# 自定义 sink 给 loguru
def qtextedit_sink(message):
    log_signal.log_received.emit(message)

# 添加 sink
logger.add(qtextedit_sink, format="{time:HH:mm:ss} | {message}", level="INFO")


class ExecutorProxy:
    """代理类，将 executor 调用转为 browser command"""
    def __init__(self, browser_service):
        self.bs = browser_service
    
    def send_danmu(self, text):
        self.bs.send_cmd(BrowserCommand.SEND_DANMU, text)
    
    def send_like(self, count):
        self.bs.send_cmd(BrowserCommand.SEND_LIKE, count)
    
    def set_page(self, page):
        pass # 不需要了

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Load app name from config
        app_title = "直播运营助手 - Chrome 增强版"
        try:
            with open(get_config_path("config.json"), "r", encoding="utf-8") as f:
                c = json.load(f)
                app_title = c.get("app", {}).get("name", app_title)
                ver = c.get("app", {}).get("version", "")
                if ver:
                    app_title += f" v{ver}"
        except:
            pass

        self.setWindowTitle(app_title)
        
        # Icon
        icon_path = get_resource_path("src/gui/favicon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            logger.warning(f"Icon file missing: {os.path.abspath(icon_path)}")

        self.resize(1200, 800)
        
        # 加载样式
        try:
            with open(get_resource_path("src/gui/styles.qss"), "r", encoding="utf-8") as f:
                self.setStyleSheet(f.read())
        except:
            pass

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Tab 1: Home
        self.main_widget = QWidget()
        self.tabs.addTab(self.main_widget, "🏠 互动控制台")
        self.layout = QHBoxLayout(self.main_widget)
        
        # Tab 2: Config
        self.config_widget = QWidget()
        self.tabs.addTab(self.config_widget, "⚙️ 高级配置 (实时修改)")

        # Tab 3: About
        self.about_widget = QWidget()
        self.tabs.addTab(self.about_widget, "ℹ️ 关于")

        # 启动浏览器服务
        self.browser_service = BrowserService()
        self.browser_service.start()
        
        self.live_controller = None
        self.voice_listener = None
        
        # 代理执行器
        self.executor_proxy = ExecutorProxy(self.browser_service)

        self._init_ui()
        self._init_config_tab(self.config_widget)
        self._init_about_tab(self.about_widget)
        self._bind_signals()
        self._subscribe_events()

    def _init_ui(self):
        # 左侧控制栏
        left_widget = QWidget()
        left_panel = QVBoxLayout(left_widget) # Use constructor to set layout immediately
        
        # Scroll Area wrap
        scroll = QScrollArea()
        scroll.setWidget(left_widget)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(340)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # 1. 账号模块
        group_auth = QGroupBox("浏览器与账号")
        layout_auth = QVBoxLayout()
        self.btn_login = QPushButton("打开浏览器 / 去登录")
        self.lbl_auth_status = QLabel("提示: 点击将启动 Chrome 窗口")
        
        self.input_nickname = QLineEdit()
        self.input_nickname.setPlaceholderText("主播助手昵称 (用于识别 @回复)")
        # Load from config if possible (Will do in _load_config or similar)
        
        layout_auth.addWidget(self.btn_login)
        layout_auth.addWidget(self.lbl_auth_status)
        layout_auth.addWidget(QLabel("主播助手昵称:"))
        layout_auth.addWidget(self.input_nickname)
        group_auth.setLayout(layout_auth)
        
        # 2. 连接设置
        group_conn = QGroupBox("直播间连接")
        layout_conn = QVBoxLayout()
        self.input_room = QLineEdit()
        self.input_room.setPlaceholderText("直播间链接/URL")
        self.btn_connect = QPushButton("进入直播间 & 开始互动")
        self.btn_connect.setObjectName("primaryInfo")
        self.btn_stop = QPushButton("停止互动")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setObjectName("warningBtn") # Use red style
        
        self.input_manual_danmu = QLineEdit()
        self.input_manual_danmu.setPlaceholderText("在此输入测试弹幕内容...")
        
        self.btn_test_send = QPushButton("发送测试弹幕")
        
        layout_conn.addWidget(QLabel("直播间地址:"))
        layout_conn.addWidget(self.input_room)
        layout_conn.addWidget(self.btn_connect)
        layout_conn.addWidget(self.btn_stop)
        layout_conn.addWidget(self.input_manual_danmu)
        layout_conn.addWidget(self.btn_test_send)
        group_conn.setLayout(layout_conn)
        
        # 3. AI 设置
        group_ai = QGroupBox("AI 模型设置")
        layout_ai = QVBoxLayout()
        self.combo_ai = QComboBox()
        self.combo_ai.addItems(["deepseek", "doubao"])
        
        self.combo_model = QComboBox()
        self.combo_model.setEditable(True) # Allow custom model input
        
        self.input_apikey = QLineEdit()
        self.input_apikey.setPlaceholderText("API Key")
        self.input_apikey.setEchoMode(QLineEdit.Password)
        
        self.btn_save_ai = QPushButton("保存并应用配置")
        
        layout_ai.addWidget(QLabel("服务商:"))
        layout_ai.addWidget(self.combo_ai)
        layout_ai.addWidget(QLabel("模型名称:"))
        layout_ai.addWidget(self.combo_model)
        layout_ai.addWidget(QLabel("API Key:"))
        layout_ai.addWidget(self.input_apikey)
        layout_ai.addWidget(self.btn_save_ai)
        group_ai.setLayout(layout_ai)
        
        # 4. 功能开关
        group_feat = QGroupBox("功能开关")
        layout_feat = QVBoxLayout()
        self.cb_welcome = QCheckBox("自动欢迎")
        self.cb_thanks = QCheckBox("感谢送礼/关注")
        self.cb_thanks_like = QCheckBox("感谢点赞")
        self.cb_auto_warmup = QCheckBox("自动暖场 (定时随机发送)") # Moved here
        
        self.cb_ai_global = QCheckBox("🟢 开启 AI (总开关)")
        self.cb_ai_link_rules = QCheckBox("   └─ 关联控制: 自动欢迎/感谢")
        self.cb_ai_chat = QCheckBox("   └─ AI 随机互动 (活跃气氛)")
        self.cb_ai_warmup = QCheckBox("   └─ AI 冷场自动暖场 (监控空闲)")
        # Deleted self.cb_auto_warmup from here
        self.cb_at_reply = QCheckBox("   └─ @我 / 关键词 回复")
        
        self.cb_voice = QCheckBox("主播语音识别")
        
        # 默认选中
        self.cb_welcome.setChecked(True)
        self.cb_thanks.setChecked(True)
        self.cb_thanks_like.setChecked(True)
        self.cb_ai_global.setChecked(True)
        self.cb_ai_link_rules.setChecked(True) # Default linked
        self.cb_ai_chat.setChecked(False) 
        self.cb_at_reply.setChecked(True)
        
        layout_feat.addWidget(self.cb_welcome)
        layout_feat.addWidget(self.cb_thanks)
        layout_feat.addWidget(self.cb_thanks_like)
        layout_feat.addWidget(self.cb_auto_warmup) # Moved here
        
        layout_feat.addWidget(self.cb_ai_global)
        layout_feat.addWidget(self.cb_ai_link_rules)
        layout_feat.addWidget(self.cb_ai_chat)
        layout_feat.addWidget(self.cb_ai_warmup)
        # Deleted addWidget(self.cb_auto_warmup)
        layout_feat.addWidget(self.cb_at_reply)
        layout_feat.addWidget(self.cb_voice)
        group_feat.setLayout(layout_feat)
        
        left_panel.addWidget(group_auth)
        left_panel.addWidget(group_conn)
        left_panel.addWidget(group_ai)
        left_panel.addWidget(group_feat)
        left_panel.addStretch()
        
        # 右侧分栏：日志 和 弹幕
        right_panel = QVBoxLayout()
        
        # 1. 弹幕显示区
        right_panel.addWidget(QLabel("🔴 实时互动/弹幕监控"))
        self.danmu_area = QTextEdit()
        self.danmu_area.setReadOnly(True)
        self.danmu_area.setStyleSheet("font-size: 14px; background-color: #fafafa;")
        self.danmu_area.setPlaceholderText("弹幕消息将显示在这里...")
        right_panel.addWidget(self.danmu_area, stretch=2) 
        
        # 2. 系统日志区
        right_panel.addWidget(QLabel("💻 系统运行日志"))
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setStyleSheet("font-family: Consolas; font-size: 12px; color: #666;")
        self.log_area.setPlaceholderText("系统日志...")
        right_panel.addWidget(self.log_area, stretch=1)
        
        # Add ScrollArea to main layout
        self.layout.addWidget(scroll)
        self.layout.addLayout(right_panel)

        # Init Model List
        self._on_provider_changed(0) 
        self._load_config_to_ui()

    def _load_config_to_ui(self):
        try:
            with open(get_config_path("config.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Load nickname
            nickname = data.get("douyin", {}).get("anchor_name", "")
            if nickname:
                self.input_nickname.setText(nickname)
            
            # Load Window Title
            app_title = data.get("app", {}).get("name", "直播运营助手")
            ver = data.get("app", {}).get("version", "")
            if ver:
                app_title += f" v{ver}"
            self.setWindowTitle(app_title)

            # Load features
            features = data.get("features", {})
            # Block signals to prevent triggering controller updates during init load
            self.blockSignals(True)
            try:
                self.cb_welcome.setChecked(features.get("enable_welcome", True))
                self.cb_thanks.setChecked(features.get("enable_thanks_gift", True))
                self.cb_thanks_like.setChecked(features.get("enable_thanks_like", True))
                
                self.cb_ai_global.setChecked(features.get("enable_ai_global", True))
                self.cb_ai_link_rules.setChecked(features.get("link_ai_to_rules", True))
                self.cb_ai_chat.setChecked(features.get("enable_ai_reply", False))
                self.cb_ai_warmup.setChecked(features.get("enable_ai_warmup", False))
                self.cb_auto_warmup.setChecked(features.get("enable_auto_warmup", False))
                self.cb_at_reply.setChecked(features.get("enable_at_reply", True))
                self.cb_voice.setChecked(features.get("enable_voice_interact", False))
            finally:
                self.blockSignals(False)

                
            # Load AI config (Optional but good UX)
            # provider = data.get("ai", {}).get("provider", "deepseek")
            # ...
        except Exception as e:
            logger.error(f"Failed to load config to UI: {e}") 

    def _bind_signals(self):
        self.btn_login.clicked.connect(self._on_login)
        self.btn_connect.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_test_send.clicked.connect(self._on_test_send)
        
        self.combo_ai.currentIndexChanged.connect(self._on_provider_changed)
        self.btn_save_ai.clicked.connect(self._on_save_ai)
        
        # Feature Toggles
        self.cb_welcome.stateChanged.connect(lambda: self._on_feature_toggled("enable_welcome", self.cb_welcome.isChecked()))
        self.cb_thanks.stateChanged.connect(lambda: self._on_feature_toggled("enable_thanks_gift", self.cb_thanks.isChecked()))
        self.cb_thanks.stateChanged.connect(lambda: self._on_feature_toggled("enable_thanks_follow", self.cb_thanks.isChecked()))
        self.cb_thanks_like.stateChanged.connect(lambda: self._on_feature_toggled("enable_thanks_like", self.cb_thanks_like.isChecked()))
        
        self.cb_ai_global.stateChanged.connect(lambda: self._on_global_ai_toggled(self.cb_ai_global.isChecked()))
        self.cb_ai_link_rules.stateChanged.connect(lambda: self._on_feature_toggled("link_ai_to_rules", self.cb_ai_link_rules.isChecked()))
        self.cb_ai_chat.stateChanged.connect(lambda: self._on_feature_toggled("enable_ai_reply", self.cb_ai_chat.isChecked()))
        self.cb_ai_warmup.stateChanged.connect(lambda: self._on_feature_toggled("enable_ai_warmup", self.cb_ai_warmup.isChecked()))
        self.cb_auto_warmup.stateChanged.connect(lambda: self._on_feature_toggled("enable_auto_warmup", self.cb_auto_warmup.isChecked()))
        self.cb_at_reply.stateChanged.connect(lambda: self._on_feature_toggled("enable_at_reply", self.cb_at_reply.isChecked()))
        self.cb_voice.stateChanged.connect(lambda: self._on_feature_toggled("enable_voice_interact", self.cb_voice.isChecked()))

        # 日志信号
        log_signal.log_received.connect(self._append_log)
        danmu_signal.danmu_received.connect(self._append_danmu)

    def _on_provider_changed(self, index):
        provider = self.combo_ai.currentText()
        self.combo_model.clear()
        if provider == "deepseek":
            self.combo_model.addItems(["deepseek-chat", "deepseek-reasoner"])
        elif provider == "doubao":
            models = [
                "doubao-seed-1-8-251215",
                "doubao-seed-1-6-251015",
                "doubao-seed-1-6-lite-251015",
                "doubao-seed-1-6-flash-250828",
                "doubao-1-5-pro-32k-250115",
                "doubao-1-5-lite-32k-250115",
                "doubao-lite-32k-240828",
                "deepseek-v3-2-251201",
                "deepseek-v3-250324",
                "deepseek-r1-250528",
                "deepseek-v3-1-terminus",
                "kimi-k2-thinking-251104"
            ]
            self.combo_model.addItems(models)
        
        # Load cached api key from config
        try:
            with open(get_config_path("config.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
            key = data.get("ai", {}).get(provider, {}).get("api_key", "")
            self.input_apikey.setText(key)
        except:
            self.input_apikey.clear()

    def _on_save_ai(self):
        provider = self.combo_ai.currentText()
        model = self.combo_model.currentText()
        api_key = self.input_apikey.text().strip()
        
        if not api_key:
            self._append_log("🔴 请输入 API Key")
            return

        if self.live_controller:
            self.live_controller.update_ai_config(provider, api_key, model)
            self.live_controller.set_anchor_name(self.input_nickname.text().strip())
            self._append_log(f"✅ AI 配置已更新: {provider} / {model}")
        else:
            self._append_log(f"💾 配置已保存 (下次启动生效): {provider} / {model}")
        
        # Save to file
        try:
            with open(get_config_path("config.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
            
            data["ai"]["provider"] = provider
            if provider not in data["ai"]:
                data["ai"][provider] = {}
            data["ai"][provider]["api_key"] = api_key
            data["ai"][provider]["model"] = model
            
            # Save nickname
            if "douyin" not in data: data["douyin"] = {}
            data["douyin"]["anchor_name"] = self.input_nickname.text().strip()
            data["douyin"]["room_url"] = self.input_room.text().strip()
            
            # Save features (Crucial for reload)
            if "features" not in data: data["features"] = {}
            data["features"]["enable_welcome"] = self.cb_welcome.isChecked()
            data["features"]["enable_thanks_gift"] = self.cb_thanks.isChecked()
            data["features"]["enable_thanks_follow"] = self.cb_thanks.isChecked()
            data["features"]["enable_thanks_like"] = self.cb_thanks_like.isChecked()
            data["features"]["enable_ai_global"] = self.cb_ai_global.isChecked()
            data["features"]["link_ai_to_rules"] = self.cb_ai_link_rules.isChecked()
            data["features"]["enable_ai_reply"] = self.cb_ai_chat.isChecked()
            data["features"]["enable_ai_warmup"] = self.cb_ai_warmup.isChecked()
            data["features"]["enable_auto_warmup"] = self.cb_auto_warmup.isChecked()
            data["features"]["enable_at_reply"] = self.cb_at_reply.isChecked()
            data["features"]["enable_voice_interact"] = self.cb_voice.isChecked()
            
            with open(get_config_path("config.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                
        except Exception as e:
            self._append_log(f"❌ 配置文件保存失败: {e}")

    def _subscribe_events(self):
        # 订阅业务事件并转发到 GUI 信号
        def on_event(event_type, data):
            # 将 dict 转为可读字符串 (支持 HTML 颜色)
            msg = ""
            if event_type == EventType.DANMU:
                u = data.get('user')
                content = data.get('content')
                # Check if self log
                nickname = self.input_nickname.text().strip()
                if nickname and u == nickname:
                    msg = f"<span style='color: #4CAF50;'>[助手弹幕] {u}: {content}</span>"
                else:
                    msg = f"<span style='color: #333333;'>[弹幕] {u}: {content}</span>"
            elif event_type == EventType.WELCOME:
                msg = f"<span style='color: #999999;'>[进场] {data.get('raw')}</span>"
            elif event_type == EventType.GIFT:
                msg = f"<span style='color: #E91E63;'>[礼物] {data.get('raw')}</span>"
            elif event_type == EventType.FOLLOW:
                msg = f"<span style='color: #FF9800;'>[关注] {data.get('raw')}</span>"
            elif event_type == "like": 
                msg = f"<span style='color: #2196F3;'>[点赞] {data.get('raw')}</span>"
            elif event_type == EventType.SPEECH:
                msg = f"<span style='color: #9C27B0;'>[语音] 主播: {data.get('text')}</span>"
            elif event_type == EventType.LOG:
                # 直接显示到系统日志区，而不是弹幕区
                log_signal.log_received.emit(data)
                return
            
            if msg:
                danmu_signal.danmu_received.emit(msg)

        EventBus.subscribe(EventType.DANMU, lambda d: on_event(EventType.DANMU, d))
        EventBus.subscribe(EventType.WELCOME, lambda d: on_event(EventType.WELCOME, d))
        EventBus.subscribe(EventType.GIFT, lambda d: on_event(EventType.GIFT, d))
        EventBus.subscribe(EventType.FOLLOW, lambda d: on_event(EventType.FOLLOW, d))
        EventBus.subscribe("like", lambda d: on_event("like", d)) # Manual subscribe
        EventBus.subscribe(EventType.SPEECH, lambda d: on_event(EventType.SPEECH, d))
        EventBus.subscribe(EventType.LOG, lambda d: on_event(EventType.LOG, d))

    @Slot(str)
    def _append_log(self, text):
        self.log_area.append(text.strip())
        # 滚动到底部
        sb = self.log_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    @Slot(str)
    def _append_danmu(self, text):
        self.danmu_area.append(text)
        sb = self.danmu_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_login(self):
        logger.info("Command: Goto Login Page")
        self.browser_service.send_cmd(BrowserCommand.GOTO, "https://www.douyin.com/")

    def _on_feature_toggled(self, feature_name, enabled):
        if self.live_controller:
            self.live_controller.set_feature(feature_name, enabled)
            self._append_log(f"🔧 功能 '{feature_name}' 已{'开启' if enabled else '关闭'}")

    def _on_global_ai_toggled(self, enabled):
        # UI Logic: Disable sub-checkboxes if global is off
        self.cb_ai_link_rules.setEnabled(enabled)
        self.cb_ai_chat.setEnabled(enabled)
        self.cb_ai_warmup.setEnabled(enabled)
        # self.cb_auto_warmup.setEnabled(enabled) # Decoupled
        self.cb_at_reply.setEnabled(enabled)
        
        # Controller Logic
        self._on_feature_toggled("enable_ai_global", enabled)

    def _on_start(self):
        room_url = self.input_room.text().strip()
        if not room_url:
            self._append_log("❌ 请输入直播间 URL")
            return
            
        full_url = room_url
        if "douyin.com" not in room_url:
            full_url = f"https://live.douyin.com/{room_url}"
        
        self._append_log(f"🚀 正在连接: {full_url}")
        
        self.btn_connect.setEnabled(False)
        self.btn_stop.setEnabled(True)
        # self.input_room.setEnabled(False)
        
        # Go to page
        self.browser_service.send_cmd(BrowserCommand.GOTO, full_url)
        self.browser_service.send_cmd(BrowserCommand.START_LISTEN)
        
        # Start Controller Logic
        self.live_controller = LiveController(self.executor_proxy)
        
        # Sync current UI state to controller
        self.live_controller.set_feature("enable_welcome", self.cb_welcome.isChecked())
        self.live_controller.set_feature("enable_thanks_gift", self.cb_thanks.isChecked())
        self.live_controller.set_feature("enable_thanks_follow", self.cb_thanks.isChecked())
        self.live_controller.set_feature("enable_thanks_like", self.cb_thanks_like.isChecked())
        
        self.live_controller.set_feature("enable_ai_global", self.cb_ai_global.isChecked())
        self.live_controller.set_feature("link_ai_to_rules", self.cb_ai_link_rules.isChecked())
        self.live_controller.set_feature("enable_ai_reply", self.cb_ai_chat.isChecked())
        self.live_controller.set_feature("enable_ai_warmup", self.cb_ai_warmup.isChecked())
        self.live_controller.set_feature("enable_auto_warmup", self.cb_auto_warmup.isChecked())
        self.live_controller.set_feature("enable_at_reply", self.cb_at_reply.isChecked())
        
        self.live_controller.set_feature("enable_voice_interact", self.cb_voice.isChecked())
        
        # Update AI config from UI
        self._on_save_ai() 
        
        self._append_log("System logic started.")
        
        # 3. 启动语音
        if self.cb_voice.isChecked():
            self.voice_listener = VoiceListener()
            self.voice_listener.start()
        logger.info(f"System logic started.")

    def _update_features(self):
        if self.live_controller:
            self.live_controller.features['enable_welcome'] = self.cb_welcome.isChecked()
            self.live_controller.features['enable_thanks_gift'] = self.cb_thanks.isChecked()
            self.live_controller.features['enable_ai_reply'] = self.cb_ai_chat.isChecked()
            # API Key passing handles in Controller init or needs setter
            # For simplicity, assuming config.json is used or user updated config manually

    def _on_test_send(self):
        text = self.input_manual_danmu.text().strip()
        if not text:
            # Default fallback msg
            msg = f"测试弹幕 {int(time.time()) % 100}"
        else:
            msg = text
            # Optional: Clear after send
            # self.input_manual_danmu.clear() 

        self._append_log(f"🟡 [测试] 尝试发送: {msg}")
        self.browser_service.send_cmd(BrowserCommand.SEND_DANMU, msg)

    def _on_stop(self):
        logger.info("Stopping interaction...")
        self.browser_service.send_cmd(BrowserCommand.STOP_LISTEN)
        
        if self.live_controller:
            self.live_controller.stop()
        if self.voice_listener:
            self.voice_listener.stop()
            
        self.btn_connect.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _init_config_tab(self, parent):
        layout = QVBoxLayout(parent)
        
        # Instructions
        layout.addWidget(QLabel("在此处修改 JSON 配置，点击下方按钮即可实时生效，无需重启程序。AI 提示词在 speech_templates.json 中。"))

        splitter = QSplitter(Qt.Horizontal)
        
        # Left: Config.json
        left_widget = QWidget()
        l_layout = QVBoxLayout(left_widget)
        l_layout.addWidget(QLabel("📝 config.json (基础配置)"))
        self.editor_config = QPlainTextEdit()
        self.editor_config.setStyleSheet("font-family: Consolas; font-size: 13px; background: #fff;")
        l_layout.addWidget(self.editor_config)
        
        # Right: Speech Templates
        right_widget = QWidget()
        r_layout = QVBoxLayout(right_widget)
        r_layout.addWidget(QLabel("💬 speech_templates.json (话术/AI指令)"))
        self.editor_templates = QPlainTextEdit()
        self.editor_templates.setStyleSheet("font-family: Consolas; font-size: 13px; background: #fff;")
        r_layout.addWidget(self.editor_templates)
        
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1) # Equal split
        splitter.setStretchFactor(1, 1)
        
        layout.addWidget(splitter, 1)
        
        btn_layout = QHBoxLayout()
        self.btn_load_cfg = QPushButton("🔄 放弃修改 (重新读取)")
        self.btn_save_reload = QPushButton("💾 保存并立即生效 (Reload)")
        self.btn_save_reload.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 15px;")
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_load_cfg)
        btn_layout.addWidget(self.btn_save_reload)
        
        layout.addLayout(btn_layout)
        
        # Bind
        self.btn_load_cfg.clicked.connect(self._load_config_files_to_editor)
        self.btn_save_reload.clicked.connect(self._save_and_reload_configs)
        
        # Initial load (Delayed to ensure files exist?)
        # Call it now
        self._load_config_files_to_editor()

    def _init_about_tab(self, parent):
        # 使用 ScrollArea 包裹所有内容，防止内容过多撑大窗口最小高度
        main_layout = QVBoxLayout(parent)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        main_layout.addWidget(scroll)
        
        content_widget = QWidget()
        scroll.setWidget(content_widget)
        
        layout = QVBoxLayout(content_widget)
        layout.setAlignment(Qt.AlignTop)
        
        # 1. Author Info
        import datetime
        date_str = datetime.date.today().strftime("%Y-%m-%d")
        
        info_group = QGroupBox("软件信息")
        info_layout = QVBoxLayout(info_group)
        
        # Models
        models = "DeepSeek, Doubao (Volcengine)"
        
        info_label = QLabel(f"""
        <h2>直播运营助手 (Chrome Enhanced)</h2>
        <p><b>作者：</b> 壹诺</p>
        <p><b>更新日期：</b> {date_str}</p>
        <p><b>当前版本：</b> v1.0.0</p>
        <p><b>对接模型：</b> {models}</p>
        <p><b>适用平台：</b> 抖音直播平台</p>
        <p><b>主要功能：</b></p>
        <ul>
            <li>自动欢迎新观众，提升直播间互动体验</li>
            <li>智能感谢礼物、关注和点赞行为</li>
            <li>AI 实时互动回复，活跃直播间氛围</li>
            <li>主播语音识别交互（暂未开发）</li>
            <li>自动暖场功能，避免直播间冷场</li>
            <li>@提及和关键词自动回复等功能</li>
        </ul>
        <p><b>设计初衷：</b> 专为中小直播间打造的智能互动工具，降低主播运营压力，提升粉丝粘性。</p>
        <p><b>技术特点：</b> 基于 Chrome 浏览器增强技术，提供稳定可靠的直播互动支持。</p>
        """)
        info_label.setStyleSheet("font-size: 14px; line-height: 1.5;")
        info_layout.addWidget(info_label)
        layout.addWidget(info_group)
        
        # 2. Disclaimer
        disc_group = QGroupBox("免责声明")
        disc_layout = QVBoxLayout(disc_group)
        disc_text = QLabel("""
        <div style='color: #d32f2f; font-weight: bold; font-size: 14px;'>
        1. 本软件仅供个人学习、研究使用，严禁任何形式的商业运营、盈利性活动或大规模分发。<br>
        2. 本软件为免费开源产品，禁止以任何方式（包括但不限于修改、打包、转售）用于商业牟利。<br>
        3. 使用者必须遵守国家相关法律法规，不得利用本软件从事任何违法违规活动。<br>
        4. 因使用本软件产生的任何直接或间接后果，包括但不限于数据丢失、账户封号、系统故障、法律责任等，均由使用者自行承担。<br>
        5. 本软件不提供任何形式的技术支持、维护服务或质量保证，使用者需自行解决使用过程中的所有问题。<br>
        6. 本软件不对任何因使用本软件而导致的损失或损害承担责任，包括但不限于经济损失、名誉损失等。<br>
        7. 使用者应自行评估并承担使用本软件的所有风险，包括但不限于兼容性风险、安全风险等。<br>
        8. 未经授权，禁止对本软件进行反向工程、修改、破解或衍生其他作品。<br>
        9. 本软件可能包含第三方开源组件，相关权利义务由各组件自身的许可证规定。<br>
        10. 使用本软件即表示您已阅读并同意上述所有条款。<br>
        </div>
        """)
        disc_text.setWordWrap(True)
        disc_layout.addWidget(disc_text)
        layout.addWidget(disc_group)
        
        # 3. Reward Code
        zsm_group = QGroupBox("赞赏作者")
        zsm_layout = QVBoxLayout(zsm_group)
        
        lbl_img = QLabel()
        try:
            pixmap = QPixmap(get_resource_path("src/gui/zsm.jpg"))
            if not pixmap.isNull():
                lbl_img.setPixmap(pixmap.scaled(300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                lbl_img.setAlignment(Qt.AlignCenter)
            else:
                lbl_img.setText("图片加载失败 (src/gui/zsm.jpg 未找到)")
        except:
             lbl_img.setText("无赞赏码图片")
             
        zsm_layout.addWidget(lbl_img)
        
        lbl_tip = QLabel("如果觉得好用，欢迎打赏支持~")
        lbl_tip.setAlignment(Qt.AlignCenter)
        zsm_layout.addWidget(lbl_tip)
        
        zsm_layout.setAlignment(Qt.AlignCenter)
        
        layout.addWidget(zsm_group)
        layout.addStretch()

    def _load_config_files_to_editor(self):
        try:
            with open(get_config_path("config.json"), "r", encoding="utf-8") as f:
                self.editor_config.setPlainText(f.read())
            
            with open(get_config_path("speech_templates.json"), "r", encoding="utf-8") as f:
                self.editor_templates.setPlainText(f.read())
                
            self._append_log("📖 配置编辑器已加载最新文件内容")
        except Exception as e:
            self._append_log(f"❌ 读取配置文件失败: {e}")

    def _save_and_reload_configs(self):
        try:
            # 1. Validation (Simple JSON check)
            cfg_txt = self.editor_config.toPlainText()
            tpl_txt = self.editor_templates.toPlainText()
            
            json.loads(cfg_txt) # Check Valid
            json.loads(tpl_txt)
            
            # 2. Save
            with open(get_config_path("config.json"), "w", encoding="utf-8") as f:
                f.write(cfg_txt)
            with open(get_config_path("speech_templates.json"), "w", encoding="utf-8") as f:
                f.write(tpl_txt)
                
            # 3. Reload Controller
            if self.live_controller:
                self.live_controller.reload_config()
                self._append_log("✅ 配置已保存并重载！新指令已生效。")
            else:
                self._append_log("💾 配置已保存。")
            
            # Refresh UI (Title, etc)
            self._load_config_to_ui()

                
        except json.JSONDecodeError as e:
            QMessageBox.critical(self, "JSON 格式错误", f"配置格式有误，请检查标点符号。\nError: {e}")
        except Exception as e:
            self._append_log(f"❌ 保存/重载失败: {e}")

    def closeEvent(self, event):
        # 关闭窗口时杀死浏览器
        self.browser_service.send_cmd(BrowserCommand.QUIT)
        event.accept()

if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
