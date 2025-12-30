import os
import json
from playwright.sync_api import sync_playwright, BrowserContext, Playwright
from src.utils.logger import get_logger

logger = get_logger()

class AuthManager:
    def __init__(self, config_path: str = "config/config.json"):
        self.cookie_file = "cookies.json"
        
        # 加载配置找到 cookie 路径
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                self.cookie_file = cfg.get("douyin", {}).get("cookie_path", "cookies.json")
        except:
            pass

    def login(self) -> bool:
        """
        启动浏览器，等待用户手动登录，然后保存 Cookie。
        """
        logger.info("Starting login process...")
        with sync_playwright() as p:
            # 启动带头模式的浏览器
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
            )
            
            page = context.new_page()
            
            # 跳转到抖音网页版
            logger.info("Navigating to douyin.com...")
            page.goto("https://www.douyin.com/")
            
            logger.info("Please login manually in the browser window.")
            # 简单的等待机制，检测是否登录成功（可以通过检测某些元素或简单等待用户关闭）
            # 这里简单实现：等待用户按回车或者检测到cookie变化（复杂），简化为等待直到用户关闭浏览器
            # 但sync模式下不好等待"直到关闭"，我们使用一个对话框或者长时间sleep等待用户操作完毕
            
            # 使用 input 阻塞 (注意：这在GUI程序中不能直接用，这里仅作为逻辑单元，GUI需另行处理调用方式)
            # 为了配合 GUI，我们假设这个方法是在独立线程运行。
            # 暂时用 sleep 模拟等待，实际可以通过 page.wait_for_selector('div.user-avatar', timeout=0) 等待头像出现
            
            try:
                # 等待用户登录成功，标志是出现了头像元素
                # 这个 selector 可能会变，需要维护
                page.wait_for_selector("#dy-user-avatar-dom", timeout=300000) # 5分钟等待时间
                logger.info("Login detected!")
                
                # 保存 cookie
                context.storage_state(path=self.cookie_file)
                logger.info(f"Cookies saved to {self.cookie_file}")
                return True
            except Exception as e:
                logger.error(f"Login failed or timed out: {e}")
                return False
            finally:
                browser.close()

    def get_context(self, p: Playwright) -> BrowserContext:
        """
        获取带有登录状态的 BrowserContext。
        注意：调用者需要负责 p (Playwright对象) 的生命周期。
        """
        if os.path.exists(self.cookie_file):
            logger.info(f"Loading cookies from {self.cookie_file}")
            # 检查文件是否为空
            if os.path.getsize(self.cookie_file) == 0:
                 logger.warning("Cookie file is empty.")
                 return p.chromium.launch(headless=False).new_context()

            browser = p.chromium.launch(headless=False) # 调试模式设为 False 可以无头
            try:
                context = browser.new_context(storage_state=self.cookie_file)
                return context
            except Exception as e:
                logger.error(f"Failed to load context with cookies: {e}")
                # 降级处理
                return browser.new_context()
        else:
            logger.warning("No cookie file found. Starting fresh session.")
            browser = p.chromium.launch(headless=False)
            return browser.new_context()

if __name__ == "__main__":
    # 测试代码
    am = AuthManager()
    print("Press Enter to start login process...")
    input()
    am.login()
