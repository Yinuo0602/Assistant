import time
import random
from playwright.sync_api import Page
from src.utils.logger import get_logger

logger = get_logger()

class DouyinExecutor:
    def __init__(self, page: Page = None):
        self.page = page

    def set_page(self, page: Page):
        self.page = page

    def send_danmu(self, text: str):
        """
        发送弹幕。
        """
        if not self.page:
            logger.warning("Executor has no page attached.")
            return

        try:
            logger.info(f"Preparing to send danmu: {text}")
            # 抖音网页版输入框选择器
            # 通常是一个 textarea，可能有特定的 class
            # 2024 常见 selector: textarea.webcast-chatroom__input
            # 或者通过 placeholder 查找
            
            # 聚焦输入框
            # 备选选择器列表，增加鲁棒性
            selectors = [
                "textarea[placeholder='说点什么...']",
                "textarea.webcast-chatroom__input",
                "textarea"
            ]
            
            input_ele = None
            for sel in selectors:
                try:
                    input_ele = self.page.wait_for_selector(sel, timeout=2000)
                    if input_ele:
                        break
                except:
                    continue
            
            if input_ele:
                input_ele.click()
                input_ele.fill(text)
                time.sleep(0.5)
                self.page.keyboard.press("Enter")
                logger.info(f"Sent: {text}")
            else:
                logger.error("Could not find chat input box.")
                
        except Exception as e:
            logger.error(f"Failed to send danmu: {e}")

    def send_like(self, count: int = 5):
        """
        发送点赞（点击屏幕）。
        """
        if not self.page:
            return

        try:
            # 点击视频区域中间
            viewport = self.page.viewport_size
            if not viewport:
                return
                
            x = viewport['width'] / 2
            y = viewport['height'] / 2
            
            # 随机一点偏移
            for _ in range(count):
                offset_x = random.randint(-50, 50)
                offset_y = random.randint(-50, 50)
                self.page.mouse.click(x + offset_x, y + offset_y)
                time.sleep(0.1)
                
            logger.debug(f"Sent {count} likes.")
        except Exception as e:
            logger.error(f"Failed to send likes: {e}")
