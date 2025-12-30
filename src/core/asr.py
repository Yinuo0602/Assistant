import time
import threading
import speech_recognition as sr
from src.core.event_bus import EventBus, EventType
from src.utils.logger import get_logger

logger = get_logger()

class VoiceListener:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.thread.start()
        logger.info("Voice listener started.")

    def stop(self):
        self.running = False
        if self.thread:
            # 这里的 join 可能会卡住，因为 listen 是阻塞的
            # SR 库有 background listen 模式，但为了控制更细，我们用循环
            pass

    def _listen_loop(self):
        """
        循环监听麦克风
        """
        # 调整环境噪音
        with self.microphone as source:
            logger.info("Adjusting for ambient noise...")
            self.recognizer.adjust_for_ambient_noise(source)

        while self.running:
            try:
                with self.microphone as source:
                    # logger.debug("Listening...")
                    # 监听一段语音，超时或静音自动切分
                    audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)
                
                # 识别
                self._transcribe(audio)

            except sr.WaitTimeoutError:
                continue
            except Exception as e:
                logger.error(f"Voice listen error: {e}")
                time.sleep(1)

    def _transcribe(self, audio):
        try:
            # 使用 Google Web Speech API (免费，需翻墙，仅作演示)
            # 生产环境建议替换为 Vosk (离线) 或 Whisper (OpenAI) 或 豆包 ASR
            # 这里为了演示方便，如果环境无法连接 Google 会报错
            # text = self.recognizer.recognize_google(audio, language="zh-CN")
            
            # 由于国内环境，recognize_google 可能不稳定。
            # 为了确保演示效果，这里建议用户后续接入特定的 API。
            # 暂时尝试使用 Sphinx (离线，效果差) 或者 placeholder。
            
            # 为了不阻碍流程，我们暂时只打印日志，实际应调用 API
            # 如果有 openai whisper，可以用:
            # text = self.recognizer.recognize_whisper(audio, language="zh")
            
            # 模拟识别结果（因为没有实际音频输入环境，或者无法确保网络）
            # text = "欢迎大家" 
            
            # 实际代码:
            try:
                text = self.recognizer.recognize_google(audio, language="zh-CN")
                if text:
                    logger.info(f"Recognized speech: {text}")
                    EventBus.publish(EventType.SPEECH, {"text": text})
            except sr.RequestError:
                 logger.warning("ASR API unavailable.")
            except sr.UnknownValueError:
                 # 没听清
                 pass

        except Exception as e:
            logger.error(f"Transcribe error: {e}")
