import threading
import queue
import time
import os
from playwright.sync_api import sync_playwright
from src.utils.logger import get_logger
from src.core.event_bus import EventBus, EventType

logger = get_logger()

class BrowserCommand:
    GOTO = "goto"
    START_LISTEN = "start_listen"
    STOP_LISTEN = "stop_listen"
    SEND_DANMU = "send_danmu"
    SEND_LIKE = "send_like"
    QUIT = "quit"

class BrowserService(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.cmd_queue = queue.Queue()
        self.is_listening = False
        self.page = None
        self.context = None
        self.playwright = None
        self.user_data_dir = os.path.join(os.getcwd(), "userdata")
        
        # Scanner helper (lazy init)
        self.last_msg_ids = set()

    def send_cmd(self, cmd_type, data=None):
        self.cmd_queue.put((cmd_type, data))

    def run(self):
        logger.info(f"浏览器服务线程已启动。UserData: {self.user_data_dir}")
        with sync_playwright() as p:
            self.playwright = p
            try:
                # 启动持久化上下文，指定 channel="chrome"
                logger.info("正在启动 Chrome...")
                self.context = p.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    channel="chrome", 
                    headless=False,
                    args=["--start-maximized"], # 最大化窗口
                    no_viewport=True,
                    accept_downloads=True
                )
                
                # 获取第一个页面或新建
                if self.context.pages:
                    self.page = self.context.pages[0]
                else:
                    self.page = self.context.new_page()
                
                logger.info("Chrome 启动成功。")

                # 主循环
                while True:
                    # 1. 处理指令 (非阻塞或带超时)
                    try:
                        # 如果正在监听，使用短超时以便快速回到 scanning
                        timeout = 0.5 if self.is_listening else 1.0
                        cmd, data = self.cmd_queue.get(timeout=timeout)
                        
                        if cmd == BrowserCommand.QUIT:
                            break
                        elif cmd == BrowserCommand.GOTO:
                            url = data
                            logger.info(f"跳转链接: {url}")
                            self.page.goto(url)
                        elif cmd == BrowserCommand.START_LISTEN:
                            self.is_listening = True
                            logger.info("弹幕监听模式已开启。")
                        elif cmd == BrowserCommand.STOP_LISTEN:
                            self.is_listening = False
                            logger.info("弹幕监听模式已关闭。")
                        elif cmd == BrowserCommand.SEND_DANMU:
                            self._do_send_danmu(data)
                        elif cmd == BrowserCommand.SEND_LIKE:
                            self._do_send_like(data)
                            
                    except queue.Empty:
                        pass
                    except Exception as e:
                        logger.error(f"处理指令出错: {e}")

                    # 2. 执行监听逻辑
                    if self.is_listening:
                        self._scan_chat()

            except Exception as e:
                logger.critical(f"浏览器服务崩溃: {e}")
            finally:
                logger.info("正在关闭浏览器上下文...")
                if self.context:
                    self.context.close()

    def _scan_chat(self):
        if not self.page: 
            return
        
        try:
            # 遍历所有 frame（为了应对 potential iframe nesting）
            # 通常主 frame 就够了，但为了健壮性...
            frames = self.page.frames
            found_any = False
            
            for frame in frames:
                try:
                    # 在每个 frame 中尝试 heuristic
                    new_messages = frame.evaluate("""() => {
                        const results = [];
                        
                        // 1. 定义特征词 (Expanded)
                        const keywords = ['来了', '送出了', '关注了', '：', ': ', '进入直播间', '点赞了'];
                        
                        // Helper: Get text with images replaced by alt
                        function getRichText(element) {
                            if (!element) return "";
                            // Deep clone to modify
                            let clone = element.cloneNode(true);
                            
                            // Replace images with alt
                            let imgs = clone.querySelectorAll('img');
                            for (let img of imgs) {
                                let alt = img.getAttribute('alt') || "";
                                if (alt) {
                                    if (alt.startsWith('[') && alt.endsWith(']')) {
                                        img.replaceWith(` ${alt} `);
                                    } else {
                                        img.replaceWith(` [${alt}] `);
                                    }
                                } else if (img.src) {
                                     // 可能是礼物图片或者表情
                                     if (img.src.includes('emoji')) {
                                         img.replaceWith(' [表情] ');
                                     } else {
                                         // 尝试从 class 判断
                                         img.replaceWith(' [图片] ');
                                     }
                                }
                            }

                            // FIX: 强制在所有子元素前后增加空格，防止灯牌和名字(Badge+Name)粘连
                            // 针对 douyin 的 dom 结构，通常是 span 挨着 span
                            clone.querySelectorAll('span, div, i, b, strong').forEach(el => {
                                 el.insertAdjacentText('beforebegin', ' ');
                                 el.insertAdjacentText('afterend', ' ');
                            });

                            return clone.innerText.replace(/[\\n\\r\\s]+/g, ' ').trim();
                        }

                        // 2. 寻找最佳容器
                        const candidates = document.querySelectorAll('div, ul, li');
                        let bestContainer = null;
                        let maxScore = 0;
                        
                        for (let el of candidates) {
                            if (el.childElementCount < 3) continue;
                            
                            let score = 0;
                            let children = el.children;
                            
                            // 优化: 只检查最后 10 条
                            let start = Math.max(0, children.length - 10);
                            let checkedCount = 0;
                            
                            for (let i = start; i < children.length; i++) {
                                let txt = children[i].innerText || "";
                                if (txt.length > 200) continue; 
                                
                                checkedCount++;
                                for (let k of keywords) {
                                    if (txt.includes(k)) {
                                        score++;
                                        break;
                                    }
                                }
                            }
                            
                            // Density check
                            if (checkedCount > 0 && (score / checkedCount) > 0.3) {
                                if (score > maxScore) {
                                    maxScore = score;
                                    bestContainer = el;
                                }
                            }
                        }
                        
                        // 3. Fallback
                        if (!bestContainer) {
                             bestContainer = document.querySelector('[data-e2e="chat-room-message-list"]');
                        }
                        
                        // 4. Extract
                        if (bestContainer) {
                            let items = bestContainer.children;
                            let start = Math.max(0, items.length - 20);
                            for (let i = start; i < items.length; i++) {
                                // 使用 getRichText 提取包含表情/礼物名的文本
                                let txt = getRichText(items[i]);
                                if (txt && txt.length > 1 && !txt.startsWith('#') && txt.length < 300) {
                                    results.push(txt);
                                }
                            }
                            return {found: true, msgs: results};
                        }
                        
                        return {found: false, msgs: []};
                    }""")
                    
                    if new_messages['found']:
                        found_any = True
                        msgs = new_messages['msgs']
                        # LOG location once
                        # if new_messages['loc']:
                        #     logger.debug(f"Found chat in frame {frame.name or 'main'} at class {new_messages['loc']}")
                        
                        for msg_text in msgs:
                            msg_hash = hash(msg_text)
                            if msg_hash in self.last_msg_ids:
                                continue
                            self.last_msg_ids.add(msg_hash)
                            if len(self.last_msg_ids) > 2000: self.last_msg_ids.clear()
                            
                            self._parse_and_publish(msg_text)
                        
                        # 如果找到了，就跳出 frame 循环 (通常只有一个聊天区)
                        break

                except Exception:
                    continue

            if not found_any:
                 # Debug only if Main Frame
                 if self.page.url.startswith("https://live.douyin.com"):
                     pass 
                     # logger.debug("Scanning failed in all frames.")

        except Exception as e:
            pass# EventBus.publish(EventType.LOG, f"[Error] Scan chat: {e}")
            pass

    def _parse_and_publish(self, text):
        # 增强解析逻辑
        text = text.strip()
        if not text: return
        
        # 清洗: 去除 [图片] 等干扰字符 (通常是 img alt 文本)
        text = text.replace("[图片]", "").replace("【图片】", "")
        # 去除多余空格
        import re
        text = re.sub(r'\s+', ' ', text).strip()
        
        # DEBUG: 查看所有流入的文本，排查礼物识别问题
        logger.debug(f"Process msg: {text}") 

        # 1. 进场 (来了 / 进入直播间)
        # 必须不包含冒号 (防止把用户发的 "来了" 当成进场消息)
        if ("来了" in text or "进入直播间" in text) and "：" not in text and ":" not in text:
            # 尝试提取名字: "张三 来了"
            # 通常格式: "等级 张三 来了"
            # 通常格式: "等级 张三 来了"
            raw_name = text
            if "来了" in text:
                raw_name = text.split("来了")[0]
            elif "进入直播间" in text:
                raw_name = text.split("进入直播间")[0]
            
            # 清洗名字 (简单取最后一个空格后的内容，如果存在)
            # 例如 "LV.10  张三" -> "张三"
            name_parts = raw_name.strip().split(" ")
            user_name = name_parts[-1] if name_parts else "用户"
            
            EventBus.publish(EventType.WELCOME, {"raw": text, "user": user_name})
            return
            
        # 2. 礼物
        if "送出了" in text or "送出" in text:
            # 防环: 如果包含 "谢谢", "感谢", "收到", "天呐", "哇", "小心意" 可能是机器人的回复，忽略
            if any(k in text for k in ["谢谢", "感谢", "收到", "天呐","哇", "小心意"]):
                 return

            # 格式可能: "张三 送出了 鲜花 x 1"
            user_name = "宝宝"
            try:
                # 简单提取: split "送出"
                split_kw = "送出了" if "送出了" in text else "送出"
                parts = text.split(split_kw, 1)
                pre_part = parts[0]
                
                # Clean user name: remove trailing ： or :
                pre_part = pre_part.strip().rstrip("：").rstrip(":")
                
                # 取最后一段作为名字 (防止前面有等级)
                user_name = pre_part.strip().split(" ")[-1]
                if not user_name:
                    user_name = "神秘大佬"
            except:
                pass
            
            EventBus.publish(EventType.GIFT, {"raw": text, "user": user_name})
            return
            
        # 3. 关注
        if "关注了" in text:
             user_name = "宝宝"
             try:
                 pre_part = text.split("关注")[0]
                 user_name = pre_part.strip().split(" ")[-1]
             except:
                 pass
             EventBus.publish(EventType.FOLLOW, {"raw": text, "user": user_name})
             return

        # 4. 点赞
        if "点赞了" in text:
             EventBus.publish(EventType.LIKE, {"raw": text}) # 需要在 EventType 加 LIKE
             return

        # 5. 弹幕
        # 寻找冒号 (英文或中文)
        # 通常格式: "等级 名字 : 内容"
        if "：" in text or ":" in text:
            # 优先用中文冒号分割
            parts = text.split("：", 1)
            if len(parts) < 2:
                parts = text.split(":", 1)
            
            if len(parts) >= 2:
                # parts[0] 是 "等级 名字"，需要清洗
                user_part = parts[0].strip()
                content = parts[1].strip()
                
                # 简单清洗用户名：去掉 "LV.1" 这种前缀
                # 假设名字是最后一部分
                user_name = user_part.split(" ")[-1]
                
                EventBus.publish(EventType.DANMU, {"user": user_name, "content": content})
                return
        
        # 如果都不是，可能是系统消息或无法解析
        # EventBus.publish(EventType.LOG, f"[Unparsed] {text}")

    def _do_send_danmu(self, text):
        try:
            # 输入框逻辑 - 尝试多种选择器
            selectors = [
                "textarea[placeholder*='与大家互动']",
                "textarea[placeholder*='与大家互动一下...']",
                "textarea[placeholder*='说点什么']",
                "div[contenteditable='true']", 
                "textarea.webcast-chatroom__input", 
                "[data-e2e='chat_input']",
                "textarea"
            ]
            el = None
            for s in selectors:
                try:
                    # 使用短超时快速尝试
                    el = self.page.wait_for_selector(s, timeout=500, state="visible")
                    if el: 
                        logger.debug(f"Found input using: {s}")
                        break
                except: 
                    continue
            
            if el:
                el.click()
                el.fill(text)
                self.page.keyboard.press("Enter")
                logger.info(f"发送弹幕成功: {text}")
            else:
                # 检查是否因为未登录
                try:
                    login_btn = self.page.query_selector("button:has-text('登录'):visible")
                    if login_btn:
                        logger.warning("发送失败: 检测到'登录'按钮，请先登录账号！")
                    else:
                        logger.warning(f"发送失败: 找不到聊天输入框 (请确认页面加载完毕且未被禁言)")
                        # Dump some debug info
                        # logger.debug(self.page.content()[:1000]) 
                except:
                    logger.warning("发送失败: 找不到聊天输入框")
                    
        except Exception as e:
            logger.error(f"发送弹幕异常: {e}")

    def _do_send_like(self, count):
        # 简单点赞逻辑
        pass
