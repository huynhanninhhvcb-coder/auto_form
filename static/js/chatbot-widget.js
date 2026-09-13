(() => {
  const toggleBtn = document.querySelector('#chatbot-toggle');
  const panel = document.querySelector('#chatbot-panel');
  const closeBtn = document.querySelector('#chatbot-close');
  const messagesEl = document.querySelector('#chatbot-messages');
  const textInput = document.querySelector('#chatbot-text-input');
  const sendBtn = document.querySelector('#chatbot-send-btn');
  const micBtn = document.querySelector('#chatbot-mic-btn');
  const clearBtn = document.querySelector('#chatbot-clear-btn');
  const statusDot = document.querySelector('#chatbot-status-dot');
  const statusText = document.querySelector('#chatbot-status-text');
  const bhytBtn = document.querySelector('#chatbot-bhyt-btn');
  const mauDonBtn = document.querySelector('#chatbot-maudon-btn');
  const khuPhoBtn = document.querySelector('#chatbot-khupho-btn');
  const danhSachBtn = document.querySelector('#chatbot-danhsach-btn');

  if (!toggleBtn || !panel) return;

  const PROCEDURES = Array.isArray(window.AUTO_FORM_CHATBOT_PROCEDURES) ? window.AUTO_FORM_CHATBOT_PROCEDURES : [];

  const SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;
  const speechRecognitionSupported = Boolean(SpeechRecognitionAPI);
  const speechOutputSupported = Boolean(window.speechSynthesis && window.SpeechSynthesisUtterance);

  let recognition = null;
  let listening = false;
  let currentUtterance = null;
  let typingIndicatorEl = null;
  let panelOpened = false;
  let welcomed = false;

  const escapeHtml = (value) => {
    const div = document.createElement('div');
    div.textContent = value;
    return div.innerHTML;
  };

  const formatMessage = (text) => {
    let safe = escapeHtml(text);
    safe = safe.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    safe = safe.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
    return safe;
  };

  const setStatus = (text, state = '') => {
    statusText.textContent = text;
    statusDot.className = `chatbot-status-dot ${state}`.trim();
  };

  const scrollToBottom = () => { messagesEl.scrollTop = messagesEl.scrollHeight; };

  const addMessage = (content, isUser, suggestions, onAskAi) => {
    const wrapper = document.createElement('div');
    wrapper.className = `chatbot-message ${isUser ? 'user' : 'bot'}`;
    const bubble = document.createElement('div');
    bubble.className = 'chatbot-message-bubble';
    bubble.innerHTML = isUser ? escapeHtml(content) : formatMessage(content);
    wrapper.append(bubble);

    if (!isUser && Array.isArray(suggestions) && suggestions.length) {
      const chips = document.createElement('div');
      chips.className = 'chatbot-quick-replies';
      suggestions.forEach((suggestion) => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chatbot-chip';
        chip.textContent = suggestion;
        chip.addEventListener('click', () => sendMessage(suggestion));
        chips.append(chip);
      });
      wrapper.append(chips);
    }

    if (!isUser && typeof onAskAi === 'function') {
      const askAiButton = document.createElement('button');
      askAiButton.type = 'button';
      askAiButton.className = 'chatbot-ask-ai-btn';
      askAiButton.textContent = '🤖 Hỏi trợ lý AI (gửi câu hỏi này tới OpenAI)';
      askAiButton.addEventListener('click', () => {
        askAiButton.disabled = true;
        onAskAi();
      });
      wrapper.append(askAiButton);
    }

    messagesEl.append(wrapper);
    scrollToBottom();
  };

  const showTyping = () => {
    if (typingIndicatorEl) return;
    typingIndicatorEl = document.createElement('div');
    typingIndicatorEl.className = 'chatbot-message bot';
    typingIndicatorEl.innerHTML = '<div class="chatbot-typing"><span></span><span></span><span></span></div>';
    messagesEl.append(typingIndicatorEl);
    scrollToBottom();
  };

  const hideTyping = () => {
    typingIndicatorEl?.remove();
    typingIndicatorEl = null;
  };

  const speak = (text) => {
    if (!speechOutputSupported) return;
    window.speechSynthesis.cancel();
    const clean = text.replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, '').replace(/[•*]/g, '');
    const utterance = new SpeechSynthesisUtterance(clean);
    utterance.lang = 'vi-VN';
    utterance.rate = 0.95;
    utterance.onstart = () => setStatus('🔊 Đang đọc…', 'speaking');
    utterance.onend = () => setStatus('Sẵn sàng');
    utterance.onerror = () => setStatus('Sẵn sàng');
    currentUtterance = utterance;
    window.speechSynthesis.speak(utterance);
  };

  const sendMessage = async (message, { useAi = false } = {}) => {
    const trimmed = message.trim();
    if (!trimmed) return;
    if (!useAi) {
      // Khi bấm "Hỏi trợ lý AI", câu hỏi đã hiển thị ở lượt gửi đầu tiên;
      // chỉ gửi lại cho ChatGPT, không lặp lại bong bóng câu hỏi của người dùng.
      addMessage(trimmed, true);
      textInput.value = '';
    }
    showTyping();
    setStatus(useAi ? '🤖 Đang hỏi trợ lý AI…' : '🔄 Đang xử lý…', 'processing');
    try {
      const response = await fetch('/api/chatbot/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: trimmed, use_ai: useAi }),
      });
      const data = await response.json();
      hideTyping();
      const askAi = data.offer_ai ? () => sendMessage(trimmed, { useAi: true }) : null;
      addMessage(data.reply, false, data.suggestions, askAi);
      if (/^xin chào|^tạm biệt/i.test(data.reply)) {
        setStatus('Sẵn sàng');
      } else {
        speak(data.reply);
      }
    } catch (_error) {
      hideTyping();
      addMessage('❌ Có lỗi xảy ra, vui lòng thử lại sau!', false);
      setStatus('❌ Lỗi kết nối', 'error');
      window.setTimeout(() => setStatus('Sẵn sàng'), 2000);
    }
  };

  const welcomeMessage = () => {
    let danhSachText = 'chưa có dữ liệu thủ tục nào, vui lòng quay lại sau';
    if (PROCEDURES.length) {
      const shown = PROCEDURES.slice(0, 5).map((name) => `"${name}"`).join(', ');
      danhSachText = shown + (PROCEDURES.length > 5 ? '…' : '');
    }
    return `🎤 Xin chào! Tôi là trợ lý thủ tục hành chính công.\n\n📋 Tôi có thể hỗ trợ tra cứu: ${danhSachText}\n👆 Gõ, nói, hoặc bấm nút micro bên dưới để hỏi tôi nhé!`;
  };

  const openPanel = () => {
    panelOpened = true;
    panel.hidden = false;
    toggleBtn.hidden = true;
    toggleBtn.setAttribute('aria-expanded', 'true');
    if (!welcomed) {
      welcomed = true;
      addMessage(welcomeMessage(), false);
    }
    textInput.focus();
  };

  const closePanel = () => {
    panelOpened = false;
    panel.hidden = true;
    toggleBtn.hidden = false;
    toggleBtn.setAttribute('aria-expanded', 'false');
    if (currentUtterance) window.speechSynthesis.cancel();
    if (listening && recognition) recognition.stop();
  };

  toggleBtn.addEventListener('click', () => (panelOpened ? closePanel() : openPanel()));
  closeBtn.addEventListener('click', closePanel);

  const submitTextInput = () => {
    void sendMessage(textInput.value);
  };
  sendBtn.addEventListener('click', submitTextInput);
  textInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      submitTextInput();
    }
  });

  clearBtn.addEventListener('click', () => {
    if (currentUtterance) window.speechSynthesis.cancel();
    fetch('/api/chatbot/clear-context', { method: 'POST' }).catch(() => {});
    messagesEl.replaceChildren();
    welcomed = false;
    addMessage(welcomeMessage(), false);
    welcomed = true;
    setStatus('Sẵn sàng');
  });

  bhytBtn.addEventListener('click', () => {
    window.open('https://baohiemxahoi.gov.vn/tracuu/Pages/tra-cuu-thoi-han-su-dung-the-bhyt.aspx', '_blank', 'noopener');
    addMessage('🏥 Đã mở trang tra cứu Bảo hiểm y tế trong tab mới!', false);
  });
  mauDonBtn.addEventListener('click', () => window.open('/mau-don', '_blank', 'noopener'));
  khuPhoBtn.addEventListener('click', () => {
    window.open('https://sites.google.com/view/phuongminhphung/trang-ch%E1%BB%A7?authuser=0', '_blank', 'noopener');
    addMessage('🗺️ Đã mở trang thông tin khu phố trong tab mới!', false);
  });
  danhSachBtn.addEventListener('click', () => sendMessage('danh sách thủ tục'));

  if (speechRecognitionSupported) {
    recognition = new SpeechRecognitionAPI();
    recognition.lang = 'vi-VN';
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      listening = true;
      micBtn.classList.add('listening');
      setStatus('🎤 Đang nghe…', 'listening');
    };
    recognition.onend = () => {
      listening = false;
      micBtn.classList.remove('listening');
    };
    recognition.onresult = (event) => {
      void sendMessage(event.results[0][0].transcript);
    };
    recognition.onerror = (event) => {
      const messageByError = {
        'not-allowed': 'Chưa cấp quyền micro.',
        'no-speech': 'Không nghe thấy giọng nói.',
      };
      setStatus(`❌ ${messageByError[event.error] || 'Lỗi nhận diện giọng nói'}`, 'error');
      window.setTimeout(() => setStatus('Sẵn sàng'), 2000);
    };
  } else {
    micBtn.disabled = true;
    micBtn.title = 'Trình duyệt này chưa hỗ trợ nhận diện giọng nói. Hãy dùng Chrome hoặc Edge mới nhất.';
  }

  micBtn.addEventListener('click', () => {
    if (!recognition) return;
    if (listening) {
      recognition.stop();
      return;
    }
    if (currentUtterance) window.speechSynthesis.cancel();
    try {
      recognition.start();
    } catch (_error) {
      setStatus('Micro đang khởi động, thử lại sau giây lát.', 'error');
    }
  });
})();
