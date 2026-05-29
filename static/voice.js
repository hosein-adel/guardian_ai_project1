// Guardian AI — Browser Voice Interaction (Push-to-Talk)
(function () {
  let stream = null;
  let audioContext = null;
  let sourceNode = null;
  let processorNode = null;
  let audioBuffers = [];
  let isRecording = false;

  const chatOutput = document.getElementById("chat-output");

  function addMessage(text, type = "system") {
    const div = document.createElement("div");
    div.className = `chat-message ${type}`;
    div.textContent = text;
    if (chatOutput) {
      chatOutput.appendChild(div);
      chatOutput.scrollTop = chatOutput.scrollHeight;
    }
  }

  function playAudioBase64(base64Data) {
    if (!base64Data) return;
    try {
      const audio = new Audio("data:audio/mp3;base64," + base64Data);
      audio.play();
    } catch (err) {
      console.error("Error playing audio:", err);
    }
  }

  function setupMicButton() {
    const btn = document.getElementById("mic-btn") || createMicButton();
    if (!btn) return;

    btn.addEventListener("mousedown", startRecording);
    btn.addEventListener("mouseup", stopRecording);
    btn.addEventListener("mouseleave", () => isRecording && stopRecording());
    
    btn.addEventListener("touchstart", (e) => { e.preventDefault(); startRecording(); });
    btn.addEventListener("touchend", (e) => { e.preventDefault(); stopRecording(); });
  }

  function createMicButton() {
    const compose = document.getElementById("chat-compose");
    if (!compose) return null;
    const btn = document.createElement("button");
    btn.id = "mic-btn";
    btn.className = "btn secondary";
    btn.textContent = "🎤 صحبت کنید";
    compose.insertBefore(btn, compose.firstChild);
    return btn;
  }

  async function startRecording() {
    if (isRecording) return;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      sourceNode = audioContext.createMediaStreamSource(stream);
      processorNode = audioContext.createScriptProcessor(4096, 1, 1);
      audioBuffers = [];

      processorNode.onaudioprocess = (e) => {
        if (isRecording) audioBuffers.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };

      sourceNode.connect(processorNode);
      processorNode.connect(audioContext.destination);

      isRecording = true;
      const btn = document.getElementById("mic-btn");
      if (btn) { btn.textContent = "🔴 در حال ضبط..."; btn.classList.add("danger"); }
    } catch (err) {
      addMessage("خطا در دسترسی به میکروفن", "system");
    }
  }

  async function stopRecording() {
    if (!isRecording) return;
    isRecording = false;
    const btn = document.getElementById("mic-btn");
    if (btn) { btn.textContent = "🎤 صحبت کنید"; btn.classList.remove("danger"); }

    const wavBlob = encodeWav(audioBuffers, audioContext.sampleRate);
    cleanup();
    if (wavBlob) await sendAudio(wavBlob);
  }

  function cleanup() {
    if (processorNode) processorNode.disconnect();
    if (sourceNode) sourceNode.disconnect();
    if (stream) stream.getTracks().forEach(t => t.stop());
    if (audioContext) audioContext.close();
  }

  function encodeWav(buffers, sampleRate) {
    if (buffers.length === 0) return null;
    const totalSamples = buffers.reduce((s, b) => s + b.length, 0);
    const result = new Float32Array(totalSamples);
    let offset = 0;
    for (const b of buffers) { result.set(b, offset); offset += b.length; }

    const buffer = new ArrayBuffer(44 + result.length * 2);
    const view = new DataView(buffer);
    const writeString = (o, s) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); };

    writeString(0, "RIFF");
    view.setUint32(4, 36 + result.length * 2, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, result.length * 2, true);

    for (let i = 0; i < result.length; i++) {
      const s = Math.max(-1, Math.min(1, result[i]));
      view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buffer], { type: "audio/wav" });
  }

  async function sendAudio(blob) {
    addMessage("... در حال پردازش", "system");
    const fd = new FormData();
    fd.append("audio", blob, "rec.wav");
    try {
      const res = await fetch("/api/voice/transcribe", { method: "POST", body: fd });
      const data = await res.json();
      const systems = chatOutput.querySelectorAll(".system");
      if (systems.length > 0) systems[systems.length - 1].remove();

      if (data.heard) addMessage(data.heard, "user");
      if (data.reply) addMessage(data.reply, "bot");
      if (data.audio) playAudioBase64(data.audio);
    } catch (err) {
      addMessage("خطا در ارسال", "system");
    }
  }

  setupMicButton();
})();
