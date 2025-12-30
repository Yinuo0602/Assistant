import json
from openai import OpenAI
from src.utils.logger import get_logger

logger = get_logger()

class AIGateway:
    def __init__(self, config_path: str = "config/config.json", speech_path: str = "config/speech_templates.json"):
        self.config = {}
        self.speech_templates = {}
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            
            # Load speech templates (optional, fail silently)
            with open(speech_path, 'r', encoding='utf-8') as f:
                 self.speech_templates = json.load(f)

        except Exception as e:
            logger.error(f"Failed to load AI config or templates: {e}")

        self.ai_conf = self.config.get("ai", {})
        self.provider = self.ai_conf.get("provider", "deepseek")
        self.client = None
        self.model_name = ""
        
        self._init_client()

    def _init_client(self):
        new_client = None
        if self.provider == "deepseek":
            conf = self.ai_conf.get("deepseek", {})
            api_key = conf.get("api_key")
            base_url = conf.get("base_url", "https://api.deepseek.com")
            self.model_name = conf.get("model", "deepseek-chat")
            
            if api_key:
                new_client = OpenAI(api_key=api_key, base_url=base_url)
                logger.info(f"DeepSeek client initialized. Model: {self.model_name}")
            else:
                logger.warning("DeepSeek API Key not found.")
                
        elif self.provider == "doubao":
            conf = self.ai_conf.get("doubao", {})
            api_key = conf.get("api_key")
            base_url = conf.get("base_url", "https://ark.cn-beijing.volces.com/api/v3") 
            self.model_name = conf.get("model", "doubao-pro-4k")
            
            if api_key:
                new_client = OpenAI(api_key=api_key, base_url=base_url)
                logger.info(f"Doubao client initialized. Model: {self.model_name}")
            else:
                logger.warning("Doubao API Key not found.")

        self.client = new_client

    def reload_templates(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                 self.speech_templates = json.load(f)
            logger.info("AIGateway templates reloaded.")
        except Exception as e:
            logger.error(f"Failed to reload AI templates: {e}")

    def update_config(self, provider, api_key, model_name):
        self.provider = provider
        self.ai_conf[provider] = {
            "api_key": api_key,
            "model": model_name
            # Preserve base_url or other settings if we read from full config again, 
            # but for runtime partial update, this is tricky. 
            # Better to just update what we have.
        }
        # Re-merge default base_url to avoid losing it if overwrite
        if provider == "deepseek":
             self.ai_conf[provider]["base_url"] = "https://api.deepseek.com"
        elif provider == "doubao":
             self.ai_conf[provider]["base_url"] = "https://ark.cn-beijing.volces.com/api/v3"

        self._init_client()

    def chat(self, user_msg: str, system_prompt: str = None) -> str:
        """
        发送消息给 AI 并获取回复。
        """
        if not self.client:
            # logger.error("AI Client not initialized.") # Removed spam
            return "" # Return empty to indicate failure silently to controller
        
        if not system_prompt:
            prompts = self.speech_templates.get("ai_prompts", {})
            system_prompt = prompts.get("system_default", "你是一个风趣幽默的直播间运营助理，负责回复观众弹幕，活跃气氛。回答简短有趣即可。")

        try:
            logger.info(f"Sending request to AI ({self.model_name})...")
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg}
                ],
                max_tokens=200, 
                temperature=0.8,
                timeout=15 
            )
            
            message = response.choices[0].message
            # Debug log raw message
            # logger.debug(f"Raw AI Message: {message}")
            
            reply = message.content
            if reply is None:
                reply = ""
            reply = reply.strip()
            
            # 兼容 DeepSeek Reasoner: 如果 content 为空但有 seasoning_content (可能在 extra_fields 或 dict 中)
            if not reply:
                # 尝试获取 reasoning_content (OpenAI python client 可能把额外字段放在 extra_fields 或 model_extra)
                reasoning = getattr(message, 'reasoning_content', "")
                if not reasoning and hasattr(message, 'model_dump'):
                    reasoning = message.model_dump().get('reasoning_content', "")
                
                if reasoning:
                    logger.info("Using reasoning content as reply since content is empty.")
                    reply = str(reasoning)

            # Handle DeepSeek Reasoner <think> tags if they exist in content
            if "<think>" in reply and "</think>" in reply:
                import re
                reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL).strip()
            
            if not reply:
                 logger.warning("AI Raw Response has no content.")
                 
            logger.info(f"AI Reply: {reply}") 
            return reply
        except Exception as e:
            logger.error(f"AI Chat Error: {e}")
            return f"AI 暂时短路了: {e}" # Return error as reply to debug in chat? No, better return empty but log.
            # Actually, user wants to know if it fails.
            # Let's just log error.
            return ""

if __name__ == "__main__":
    # Test
    gateway = AIGateway()
    print(gateway.chat("主播几岁了？"))
