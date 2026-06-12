from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["webchat"])

@router.get("/chat", response_class=HTMLResponse, summary="웹챗 UI 페이지")
async def webchat_page():
    return HTMLResponse(content=WEBCHAT_HTML)

WEBCHAT_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>현대자동화 AI 부품 도우미</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box;}
  body{font-family:'Noto Sans KR',sans-serif;background:#f0f2f5;display:flex;flex-direction:column;height:100dvh;}

  /* 헤더 */
  .header{background:#03C75A;padding:14px 16px;display:flex;align-items:center;gap:10px;box-shadow:0 2px 8px rgba(0,0,0,.15);}
  .header-avatar{width:38px;height:38px;border-radius:50%;background:rgba(255,255,255,.25);display:flex;align-items:center;justify-content:center;font-size:20px;}
  .header-info{flex:1;}
  .header-title{font-size:16px;font-weight:700;color:#fff;margin:0;}
  .header-sub{font-size:11px;color:rgba(255,255,255,.85);margin:1px 0 0;}
  .online-dot{width:8px;height:8px;background:#fff;border-radius:50%;display:inline-block;margin-right:4px;}

  /* 채팅창 */
  .chat-area{flex:1;overflow-y:auto;padding:16px 12px;display:flex;flex-direction:column;gap:10px;}
  .msg-row{display:flex;align-items:flex-end;gap:8px;}
  .msg-row.user{flex-direction:row-reverse;}
  .bot-avatar{width:30px;height:30px;border-radius:50%;background:#03C75A;display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0;}
  .bubble{max-width:75%;padding:10px 14px;border-radius:18px;font-size:14px;line-height:1.55;word-break:break-word;white-space:pre-wrap;}
  .bubble.bot{background:#fff;color:#191919;border-bottom-left-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.1);}
  .bubble.user{background:#03C75A;color:#fff;border-bottom-right-radius:4px;}
  .time{font-size:10px;color:#aaa;margin-bottom:2px;}

  /* 퀵리플라이 */
  .quick-wrap{padding:6px 12px;display:flex;gap:6px;flex-wrap:wrap;}
  .quick-btn{background:#fff;border:1.5px solid #03C75A;color:#03C75A;border-radius:20px;padding:6px 14px;font-size:12px;cursor:pointer;white-space:nowrap;transition:all .15s;}
  .quick-btn:hover{background:#03C75A;color:#fff;}

  /* 입력창 */
  .input-area{background:#fff;padding:10px 12px;display:flex;gap:8px;align-items:center;border-top:1px solid #e8e8e8;}
  .input-box{flex:1;border:1.5px solid #e0e0e0;border-radius:22px;padding:9px 16px;font-size:14px;outline:none;resize:none;height:42px;line-height:1.4;font-family:inherit;}
  .input-box:focus{border-color:#03C75A;}
  .send-btn{width:42px;height:42px;border-radius:50%;background:#03C75A;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:background .15s;}
  .send-btn:hover{background:#02b350;}
  .send-btn svg{width:18px;height:18px;fill:#fff;}

  /* 로딩 */
  .typing{display:flex;gap:4px;padding:12px 16px;background:#fff;border-radius:18px;border-bottom-left-radius:4px;width:fit-content;box-shadow:0 1px 3px rgba(0,0,0,.1);}
  .typing span{width:7px;height:7px;background:#ccc;border-radius:50%;animation:bounce 1.2s infinite;}
  .typing span:nth-child(2){animation-delay:.2s;}
  .typing span:nth-child(3){animation-delay:.4s;}
  @keyframes bounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-6px)}}

  /* 안내 배너 */
  .banner{background:#E8F9EE;border-left:3px solid #03C75A;margin:0 12px 4px;border-radius:6px;padding:8px 12px;font-size:12px;color:#1a6636;line-height:1.5;}
</style>
</head>
<body>

<div class="header">
  <div class="header-avatar">🤖</div>
  <div class="header-info">
    <p class="header-title">현대자동화 AI 부품 도우미</p>
    <p class="header-sub"><span class="online-dot"></span>24시간 자동응답 · FA 산업 부품 전문</p>
  </div>
</div>

<div class="chat-area" id="chatArea"></div>

<div class="quick-wrap" id="quickWrap">
  <button class="quick-btn" onclick="sendQuick('단종 대체품 찾기')">🔄 단종 대체품</button>
  <button class="quick-btn" onclick="sendQuick('제품 규격 알려줘')">📐 규격/사이즈</button>
  <button class="quick-btn" onclick="sendQuick('고장 알람 진단')">⚠️ 고장 알람</button>
  <button class="quick-btn" onclick="sendQuick('현대자동화 위치 알려줘')">📍 위치 안내</button>
</div>

<div class="input-area">
  <textarea class="input-box" id="inputBox" placeholder="모델명이나 질문을 입력하세요..." rows="1"></textarea>
  <button class="send-btn" id="sendBtn" onclick="sendMessage()">
    <svg viewBox="0 0 24 24"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg>
  </button>
</div>

<script>
const API_URL = window.location.origin + '/api/chat/';
let isLoading = false;

function getTime(){
  const n=new Date();
  return n.getHours().toString().padStart(2,'0')+':'+n.getMinutes().toString().padStart(2,'0');
}

function addBotMsg(text){
  const area=document.getElementById('chatArea');
  const row=document.createElement('div');
  row.className='msg-row';
  row.innerHTML=`<div class="bot-avatar">🤖</div>
    <div>
      <div class="bubble bot">${text.replace(/\\*\\*(.*?)\\*\\*/g,'<strong>$1</strong>').replace(/\\n/g,'<br>')}</div>
      <div class="time">${getTime()}</div>
    </div>`;
  area.appendChild(row);
  area.scrollTop=area.scrollHeight;
}

function addUserMsg(text){
  const area=document.getElementById('chatArea');
  const row=document.createElement('div');
  row.className='msg-row user';
  row.innerHTML=`<div>
      <div class="bubble user">${text}</div>
      <div class="time" style="text-align:right">${getTime()}</div>
    </div>`;
  area.appendChild(row);
  area.scrollTop=area.scrollHeight;
}

function showTyping(){
  const area=document.getElementById('chatArea');
  const row=document.createElement('div');
  row.className='msg-row';
  row.id='typingRow';
  row.innerHTML=`<div class="bot-avatar">🤖</div>
    <div class="typing"><span></span><span></span><span></span></div>`;
  area.appendChild(row);
  area.scrollTop=area.scrollHeight;
}

function removeTyping(){
  const t=document.getElementById('typingRow');
  if(t) t.remove();
}

async function sendMessage(){
  if(isLoading) return;
  const input=document.getElementById('inputBox');
  const text=input.value.trim();
  if(!text) return;
  input.value='';
  input.style.height='42px';
  addUserMsg(text);
  await callAPI(text);
}

async function sendQuick(text){
  if(isLoading) return;
  addUserMsg(text);
  await callAPI(text);
}

async function callAPI(text){
  isLoading=true;
  document.getElementById('sendBtn').style.opacity='0.5';
  showTyping();
  try{
    const res=await fetch(API_URL,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:text,channel:'webchat'})
    });
    const data=await res.json();
    removeTyping();
    addBotMsg(data.reply||'죄송합니다. 잠시 후 다시 시도해 주세요.');
  }catch(e){
    removeTyping();
    addBotMsg('일시적인 오류가 발생했습니다.\\n현대자동화(051-000-0000)로 문의해 주세요.');
  }
  isLoading=false;
  document.getElementById('sendBtn').style.opacity='1';
}

// Enter 전송
document.getElementById('inputBox').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage();}
});

// 웰컴 메시지
window.onload=()=>{
  addBotMsg('안녕하세요! 현대자동화 AI 부품 도우미입니다. 👋\\n\\nFA 산업 자동화 부품에 대해 무엇이든 질문해 주세요!\\n\\n🔄 단종품 대체 모델 안내\\n📐 규격 / 사이즈 / 동작 사양\\n⚠️ 고장 알람 코드 진단\\n📍 위치 안내 및 재고 확인');
};
</script>
</body>
</html>"""
