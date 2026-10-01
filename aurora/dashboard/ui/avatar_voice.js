(() => {
  "use strict";

  const STATES = Object.freeze({
    IDLE: "IDLE",
    LISTENING: "LISTENING",
    THINKING: "THINKING",
    SPEAKING: "SPEAKING",
    ERROR: "ERROR",
    DISCONNECTED: "DISCONNECTED"
  });

  class AvatarVoiceError extends Error {
    constructor(code, message) {
      super(message);
      this.name = "AvatarVoiceError";
      this.code = code;
    }
  }

  class BrowserSpeechProvider {
    constructor({ language = "es-ES", onState, onTranscript, onError } = {}) {
      this.language = language;
      this.onState = onState || (() => {});
      this.onTranscript = onTranscript || (() => {});
      this.onError = onError || (() => {});
      this.recognition = null;
      this.stream = null;
      this.synthesis = "speechSynthesis" in window ? window.speechSynthesis : null;
      this.currentUtterance = null;
      this._buildRecognition();
    }

    get capabilities() {
      return Object.freeze({
        speech_output: Boolean(this.synthesis),
        speech_input: Boolean(this.recognition),
        microphone_permission: Boolean(
          navigator.mediaDevices && navigator.mediaDevices.getUserMedia
        )
      });
    }

    async startListening() {
      if (!this.recognition) {
        throw new AvatarVoiceError(
          "speech_input_unavailable",
          "Speech recognition is not available in this browser."
        );
      }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new AvatarVoiceError(
          "microphone_unavailable",
          "Microphone access is not available in this browser."
        );
      }
      this.stopSpeaking();
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this.onState(STATES.LISTENING);
        this.recognition.start();
      } catch (error) {
        this._releaseStream();
        this.onState(STATES.ERROR);
        const wrapped = new AvatarVoiceError(
          "microphone_denied",
          error && error.message ? error.message : "Microphone permission failed."
        );
        this.onError(wrapped);
        throw wrapped;
      }
    }

    stopListening() {
      if (this.recognition) {
        try {
          this.recognition.stop();
        } catch (_) {
          // Recognition may already be stopped.
        }
      }
      this._releaseStream();
      if (this._stateIs(STATES.LISTENING)) {
        this.onState(STATES.IDLE);
      }
    }

    speak(text, { language = this.language, rate = 1, pitch = 1 } = {}) {
      const content = String(text == null ? "" : text).trim();
      if (!content) return false;
      if (!this.synthesis) {
        throw new AvatarVoiceError(
          "speech_output_unavailable",
          "Speech synthesis is not available in this browser."
        );
      }

      this.stopListening();
      this.synthesis.cancel();

      const utterance = new SpeechSynthesisUtterance(content);
      utterance.lang = language;
      utterance.rate = rate;
      utterance.pitch = pitch;
      utterance.onstart = () => this.onState(STATES.SPEAKING);
      utterance.onend = () => {
        this.currentUtterance = null;
        this.onState(STATES.IDLE);
      };
      utterance.onerror = (event) => {
        this.currentUtterance = null;
        const error = new AvatarVoiceError(
          "speech_output_failed",
          event && event.error ? String(event.error) : "Speech synthesis failed."
        );
        this.onState(STATES.ERROR);
        this.onError(error);
      };
      this.currentUtterance = utterance;
      this.synthesis.speak(utterance);
      return true;
    }

    stopSpeaking() {
      if (!this.synthesis) return;
      this.synthesis.cancel();
      this.currentUtterance = null;
      if (this._stateIs(STATES.SPEAKING)) {
        this.onState(STATES.IDLE);
      }
    }

    setThinking() {
      this.stopListening();
      this.stopSpeaking();
      this.onState(STATES.THINKING);
    }

    setDisconnected() {
      this.stopListening();
      this.stopSpeaking();
      this.onState(STATES.DISCONNECTED);
    }

    setIdle() {
      this.stopListening();
      this.stopSpeaking();
      this.onState(STATES.IDLE);
    }

    dispose() {
      this.stopListening();
      this.stopSpeaking();
      this.recognition = null;
      this.currentUtterance = null;
    }

    _buildRecognition() {
      const Recognition =
        window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!Recognition) return;

      this.recognition = new Recognition();
      this.recognition.lang = this.language;
      this.recognition.interimResults = true;
      this.recognition.maxAlternatives = 1;

      this.recognition.onresult = (event) => {
        const results = Array.from(event.results || []);
        const transcript = results
          .map((result) => result[0] && result[0].transcript ? result[0].transcript : "")
          .join(" ")
          .trim();
        if (!transcript) return;

        const last = results[results.length - 1];
        const isFinal = Boolean(last && last.isFinal);
        this.onTranscript({ text: transcript, final: isFinal });

        if (isFinal) {
          this.onState(STATES.THINKING);
        }
      };

      this.recognition.onerror = (event) => {
        const code = event && event.error ? String(event.error) : "recognition_failed";
        const error = new AvatarVoiceError("speech_input_failed", code);
        this._releaseStream();
        this.onState(STATES.ERROR);
        this.onError(error);
      };

      this.recognition.onend = () => {
        this._releaseStream();
        if (this._stateIs(STATES.LISTENING)) {
          this.onState(STATES.IDLE);
        }
      };
    }

    _releaseStream() {
      if (!this.stream) return;
      for (const track of this.stream.getTracks()) track.stop();
      this.stream = null;
    }

    _stateIs(state) {
      return document.documentElement.dataset.avatarState === state;
    }
  }

  class AvatarVoiceAdapter {
    constructor({
      root,
      transcriptTarget = null,
      statusTarget = null,
      language = "es-ES",
      onTranscript = null,
      onState = null
    } = {}) {
      if (!root) {
        throw new AvatarVoiceError("root_required", "Avatar root is required.");
      }
      this.root = root;
      this.transcriptTarget = transcriptTarget;
      this.statusTarget = statusTarget;
      this.listeners = {
        onTranscript: onTranscript || (() => {}),
        onState: onState || (() => {})
      };
      this.state = STATES.IDLE;
      this.provider = new BrowserSpeechProvider({
        language,
        onState: (state) => this.setState(state),
        onTranscript: (payload) => {
          this._renderTranscript(payload);
          this.listeners.onTranscript(payload);
        },
        onError: (error) => this._renderError(error)
      });
      this.setState(STATES.IDLE);
    }

    get states() {
      return STATES;
    }

    get capabilities() {
      return this.provider.capabilities;
    }

    async startListening() {
      try {
        await this.provider.startListening();
      } catch (error) {
        this.setState(STATES.ERROR);
        throw error;
      }
    }

    stopListening() {
      this.provider.stopListening();
    }

    setThinking() {
      this.provider.setThinking();
    }

    speak(text, options) {
      try {
        return this.provider.speak(text, options);
      } catch (error) {
        this.setState(STATES.ERROR);
        throw error;
      }
    }

    stopSpeaking() {
      this.provider.stopSpeaking();
    }

    setDisconnected() {
      this.provider.setDisconnected();
    }

    setIdle() {
      this.provider.setIdle();
    }

    setState(state) {
      if (!Object.values(STATES).includes(state)) {
        throw new AvatarVoiceError("invalid_state", "Unknown avatar state: " + state);
      }
      this.state = state;
      this.root.dataset.avatarState = state;
      if (this.statusTarget) {
        this.statusTarget.textContent = state;
        this.statusTarget.dataset.state = state;
      }
      this.listeners.onState(state);
    }

    _renderTranscript(payload) {
      if (!this.transcriptTarget) return;
      this.transcriptTarget.textContent = payload.text;
      this.transcriptTarget.dataset.final = String(Boolean(payload.final));
    }

    _renderError(error) {
      if (!this.transcriptTarget) return;
      this.transcriptTarget.textContent = "Voice unavailable: " + error.message;
    }

    dispose() {
      this.provider.dispose();
      this.setState(STATES.IDLE);
    }
  }

  window.AuroraAvatarVoiceAdapter = Object.freeze({
    AvatarVoiceAdapter,
    AvatarVoiceError,
    BrowserSpeechProvider,
    STATES
  });
})();
