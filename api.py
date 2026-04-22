# server.py - 本地反代服務 (端口5003) - 強化版
import json
import logging
import requests
import time
import random
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

# 配置日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chatjimmy-proxy")

app = FastAPI()

# 添加 CORS - 允許所有來源和方法
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# chatjimmy.ai 的 API 地址
CHATJIMMY_URL = "https://chatjimmy.ai/api/chat"
# 固定的模型名稱
MODEL_NAME = "llama3.1-8B"

# 默認的 API key (dummy)
DEFAULT_API_KEY = "dummy"

# 多個 User-Agent 輪換，避免被識別
USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]

# 從你的 curl 命令中提取的完整 headers
def get_headers():
    """獲取隨機的請求頭，模擬不同設備"""
    return {
        "authority": "chatjimmy.ai",
        "accept": "*/*",
        "accept-language": "zh-HK,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "content-type": "application/json",
        "origin": "https://chatjimmy.ai",
        "referer": "https://chatjimmy.ai/",
        "sec-ch-ua": '"Chromium";v="137", "Not/A)Brand";v="24"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": random.choice(USER_AGENTS),
        "x-requested-with": "XMLHttpRequest",  # 模擬 AJAX 請求
    }

def messages_prepare(messages: list) -> list:
    """準備發送給 chatjimmy 的消息格式"""
    chatjimmy_messages = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        
        # 處理可能的 content 數組格式
        if isinstance(content, list):
            texts = [item.get("text", "") for item in content if item.get("type") == "text"]
            content = "\n".join(texts)
        
        # 確保內容不為空
        if not content:
            content = " "
        
        chatjimmy_messages.append({
            "role": role,
            "content": str(content)
        })
    
    return chatjimmy_messages

def parse_chatjimmy_response(response_text: str):
    """解析 chatjimmy 的響應，分離內容和統計信息"""
    # 處理多行響應
    lines = response_text.strip().split('\n')
    content_parts = []
    stats = {}
    
    for line in lines:
        if "<|stats|>" in line:
            parts = line.split("<|stats|>")
            if parts[0].strip():
                content_parts.append(parts[0].strip())
            try:
                if len(parts) > 1 and parts[1].strip():
                    stats = json.loads(parts[1])
            except:
                pass
        else:
            if line.strip():
                content_parts.append(line.strip())
    
    content = "\n".join(content_parts)
    return content, stats

@app.get("/v1/models")
async def list_models():
    """列出可用模型"""
    return {
        "object": "list",
        "data": [{
            "id": MODEL_NAME,
            "object": "model",
            "created": 1677610602,
            "owned_by": "chatjimmy",
            "permission": []
        }]
    }

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """處理聊天完成請求"""
    request_id = f"req_{int(time.time())}_{random.randint(1000, 9999)}"
    
    try:
        # 檢查 API key (雖然是 dummy，但保留格式)
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning(f"[{request_id}] 缺少 Authorization header")
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        
        api_key = auth_header.replace("Bearer ", "", 1).strip()
        if api_key != DEFAULT_API_KEY:
            logger.info(f"[{request_id}] 使用非默認 API key: {api_key[:8]}...")
        
        # 解析請求體
        try:
            req_data = await request.json()
        except Exception as e:
            logger.error(f"[{request_id}] 解析請求 JSON 失敗: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON")
        
        messages = req_data.get("messages", [])
        stream = req_data.get("stream", False)
        model = req_data.get("model", MODEL_NAME)
        
        if not messages:
            logger.error(f"[{request_id}] 請求中沒有 messages")
            raise HTTPException(status_code=400, detail="Messages are required")
        
        # 準備發送給 chatjimmy 的數據
        chatjimmy_messages = messages_prepare(messages)
        payload = {
            "messages": chatjimmy_messages,
            "chatOptions": {
                "selectedModel": MODEL_NAME,
                "systemPrompt": "",
                "topK": 8
            },
            "attachment": None
        }
        
        logger.info(f"[{request_id}] 發送請求到 chatjimmy, 消息數: {len(chatjimmy_messages)}")
        
        # 獲取隨機 headers
        headers = get_headers()
        
        # 發送請求到 chatjimmy
        session = requests.Session()
        response = session.post(
            CHATJIMMY_URL,
            headers=headers,
            json=payload,
            stream=stream,
            timeout=60
        )
        
        if response.status_code != 200:
            error_text = response.text[:500]  # 只取前500字符
            logger.error(f"[{request_id}] chatjimmy 返回錯誤 {response.status_code}: {error_text}")
            raise HTTPException(
                status_code=502, 
                detail=f"Upstream server error: {response.status_code}"
            )
        
        created_time = int(time.time())
        completion_id = f"chatcmpl-{request_id}"
        
        # 處理流式響應
        if stream:
            async def generate_stream():
                full_content = ""
                chunk_count = 0
                
                try:
                    for line in response.iter_lines():
                        if line:
                            try:
                                line_text = line.decode('utf-8')
                                content, stats = parse_chatjimmy_response(line_text)
                                
                                if content:
                                    # 構建 OpenAI 格式的 chunk
                                    openai_chunk = {
                                        "id": completion_id,
                                        "object": "chat.completion.chunk",
                                        "created": created_time,
                                        "model": model,
                                        "choices": [{
                                            "index": 0,
                                            "delta": {"content": content},
                                            "finish_reason": None
                                        }]
                                    }
                                    yield f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                    full_content += content
                                    chunk_count += 1
                                
                                # 如果有統計信息且表示完成，發送最後一塊
                                if stats.get("done"):
                                    final_chunk = {
                                        "id": completion_id,
                                        "object": "chat.completion.chunk",
                                        "created": created_time,
                                        "model": model,
                                        "choices": [{
                                            "index": 0,
                                            "delta": {},
                                            "finish_reason": stats.get("done_reason", "stop")
                                        }],
                                        "usage": {
                                            "prompt_tokens": stats.get("prefill_tokens", 0),
                                            "completion_tokens": stats.get("decode_tokens", 0),
                                            "total_tokens": stats.get("total_tokens", 0)
                                        }
                                    }
                                    yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
                                    yield "data: [DONE]\n\n"
                                    logger.info(f"[{request_id}] 流式響應完成, 發送 {chunk_count} 個 chunks")
                                    
                            except Exception as e:
                                logger.error(f"[{request_id}] 處理流式響應行時出錯: {e}")
                                continue
                                
                except Exception as e:
                    logger.error(f"[{request_id}] 流式響應生成器錯誤: {e}")
                finally:
                    response.close()
            
            return StreamingResponse(
                generate_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Content-Type": "text/event-stream",
                }
            )
        
        # 處理非流式響應
        else:
            full_response = ""
            line_count = 0
            
            for line in response.iter_lines():
                if line:
                    try:
                        line_text = line.decode('utf-8')
                        full_response += line_text
                        line_count += 1
                    except Exception as e:
                        logger.error(f"[{request_id}] 處理響應行時出錯: {e}")
            
            content, stats = parse_chatjimmy_response(full_response)
            
            logger.info(f"[{request_id}] 非流式響應完成, 收到 {line_count} 行, 內容長度: {len(content)}")
            
            # 構建 OpenAI 格式的完整響應
            openai_response = {
                "id": completion_id,
                "object": "chat.completion",
                "created": created_time,
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content
                    },
                    "finish_reason": stats.get("done_reason", "stop")
                }],
                "usage": {
                    "prompt_tokens": stats.get("prefill_tokens", 0),
                    "completion_tokens": stats.get("decode_tokens", 0),
                    "total_tokens": stats.get("total_tokens", 0)
                }
            }
            
            return JSONResponse(content=openai_response)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{request_id}] 處理請求時出錯: {e}")
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

@app.get("/")
async def root():
    """根路徑返回服務信息"""
    return {
        "service": "chatjimmy OpenAI Proxy",
        "version": "2.0",
        "models": [MODEL_NAME],
        "usage": "POST /v1/chat/completions with Authorization: Bearer dummy",
        "features": ["streaming", "non-streaming", "CORS enabled"]
    }

@app.get("/health")
async def health():
    """健康檢查端點"""
    results = {}
    
    # 測試 chatjimmy 連接
    try:
        headers = get_headers()
        test_response = requests.post(
            CHATJIMMY_URL,
            headers=headers,
            json={
                "messages": [{"role": "user", "content": "test"}],
                "chatOptions": {
                    "selectedModel": MODEL_NAME,
                    "systemPrompt": "",
                    "topK": 8
                },
                "attachment": None
            },
            timeout=5
        )
        
        if test_response.status_code == 200:
            results["chatjimmy"] = "connected"
            # 嘗試解析響應
            try:
                first_line = next(test_response.iter_lines()).decode('utf-8')
                results["sample_response"] = first_line[:100] + "..."
            except:
                pass
        else:
            results["chatjimmy"] = f"error {test_response.status_code}"
            results["response"] = test_response.text[:200]
    except Exception as e:
        results["chatjimmy"] = f"failed: {str(e)}"
    
    # 檢查服務狀態
    if results.get("chatjimmy") == "connected":
        return {"status": "healthy", **results}
    else:
        return {"status": "degraded", **results}

if __name__ == "__main__":
    logger.info("啟動 chatjimmy 代理服務器 on port 5003")
    logger.info(f"模型: {MODEL_NAME}")
    logger.info(f"API 端點: http://localhost:5003/v1/chat/completions")
    uvicorn.run(app, host="0.0.0.0", port=5003)