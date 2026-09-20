/* InterviewIQ — Real Interview interaction layer.
   Adds the AI interviewer avatar (idle / thinking / speaking / listening /
   processing), wires the three response modes (Text -> Text, Voice -> Text,
   Voice -> Voice) into the existing server flow, and keeps the existing
   server-enforced timer behavior: on expiry it stops speech + recognition,
   flushes any live transcript into the answer box, and auto-submits exactly
   as before.

   All speech stays inside the browser (SpeechSynthesis / Web Speech API).
   Nothing is sent to an external voice provider and no transcripts are
   logged. Mode preference lives only in sessionStorage (per-tab, ephemeral).

   No-API design:
   - Text -> Text   : question shown as text, answer typed.
   - Voice -> Text  : question read aloud, answer typed.
   - Voice -> Voice : question read aloud, answer captured by microphone,
                      transcript edited, then submitted as normal text.
   Everything degrades to the traditional text flow when a browser API is
   missing or a permission is denied. */
(function () {
  "use strict";

  var MODE_KEY = "interviewiq:response-mode";
  var ANSWER_MAX = 5000;

  function storageGet() {
    try {
      return sessionStorage.getItem(MODE_KEY);
    } catch (e) {
      return null;
    }
  }
  function storageSet(mode) {
    try {
      sessionStorage.setItem(MODE_KEY, mode);
    } catch (e) {
      /* ignore storage failures */
    }
  }

  function hasTTS() {
    return !!window.speechSynthesis &&
      "SpeechSynthesisUtterance" in window;
  }
  function hasSTT() {
    return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  function isSupportedMode(mode) {
    if (mode === "voice-voice") return hasTTS() && hasSTT();
    if (mode === "voice-text") return hasTTS();
    return mode === "text-text";
  }

  function bestSupportedMode() {
    if (hasTTS() && hasSTT()) return "voice-voice";
    if (hasTTS()) return "voice-text";
    return "text-text";
  }

  function prettify(mode) {
    if (mode === "voice-voice") return "Voice \u2192 Voice";
    if (mode === "voice-text") return "Voice \u2192 Text";
    return "Text \u2192 Text";
  }

  /* ------------------------------------------------------------------------
     Config page — response mode picker.
     ------------------------------------------------------------------------ */
  function initConfig() {
    var cards = document.querySelectorAll("[data-mode-card]");
    if (!cards.length) return;

    var chosen = null;

    function select(mode) {
      chosen = mode;
      Array.prototype.forEach.call(cards, function (card) {
        var input = card.querySelector("input[name='response_mode']");
        var active = card.getAttribute("data-mode-card") === mode;
        card.classList.toggle("is-selected", active);
        if (input) input.checked = active;
      });
    }

    var stored = storageGet();
    var fallback = bestSupportedMode();

    Array.prototype.forEach.call(cards, function (card) {
      var mode = card.getAttribute("data-mode-card");
      var input = card.querySelector("input[name='response_mode']");
      var note = card.querySelector("[data-mode-note]");
      if (!isSupportedMode(mode)) {
        card.classList.add("is-disabled");
        if (input) input.disabled = true;
        if (note) note.textContent =
          "Not supported in this browser \u2014 use one of the others.";
      }
      card.addEventListener("click", function () {
        if (card.classList.contains("is-disabled")) return;
        select(mode);
      });
    });

    select(isSupportedMode(stored) ? stored : fallback);

    var form = cards[0].closest("form");
    if (form) {
      form.addEventListener("submit", function () {
        storageSet(chosen);
      });
    }

    // Company Presets (Phase 10 / Stage 1): keep the context hint in sync
    // with the selected option; server renders the default when JS is off.
    var companySelect = document.querySelector("[data-company-select]");
    var companyNote = document.querySelector("[data-company-context]");
    if (companySelect && companyNote) {
      function updateCompanyNote() {
        var option = companySelect.options[companySelect.selectedIndex];
        var text = option ? option.getAttribute("data-company-short") : "";
        companyNote.textContent = text || "";
      }
      companySelect.addEventListener("change", updateCompanyNote);
      updateCompanyNote();
    }
  }

  /* ------------------------------------------------------------------------
     Speech synthesis layer — browser-native only.
     ------------------------------------------------------------------------ */
  function createSpeech() {
    var muted = false;

    function synth() {
      return hasTTS() ? window.speechSynthesis : null;
    }

    function pickVoice() {
      var engine = synth();
      if (!engine) return null;
      var voices = [];
      try {
        voices = engine.getVoices() || [];
      } catch (e) {
        voices = [];
      }
      var preferred = [
        "Google US English", "Microsoft Aria Natural", "Microsoft Jenny Natural",
        "Microsoft Zira", "Samantha", "Daniel", "Google UK English Female",
        "Karen", "Moira",
      ];
      for (var p = 0; p < preferred.length; p++) {
        for (var v = 0; v < voices.length; v++) {
          if (voices[v].name === preferred[p]) return voices[v];
        }
      }
      for (var k = 0; k < voices.length; k++) {
        if (/^en/i.test(voices[k].lang || "")) return voices[k];
      }
      return voices.length ? voices[0] : null;
    }

    function stop() {
      var engine = synth();
      if (engine) {
        try {
          engine.cancel();
        } catch (e) {
          /* ignore */
        }
      }
    }

    function speak(text, onStart, onEnd, onError) {
      var engine = synth();
      if (!engine || !text) {
        if (onError) onError();
        return;
      }
      stop();
      var utterance = new SpeechSynthesisUtterance(text);
      var voice = pickVoice();
      if (voice) utterance.voice = voice;
      utterance.lang = (voice && voice.lang) || "en-US";
      utterance.rate = 1.02;
      utterance.pitch = 1;
      var started = false;
      utterance.onstart = function () {
        started = true;
        if (onStart) onStart();
      };
      utterance.onend = function () {
        if (onEnd) onEnd();
      };
      utterance.onerror = function (event) {
        if (event.error === "canceled") {
          if (onEnd) onEnd();
          return;
        }
        if (onError) onError();
      };
      engine.speak(utterance);
    }

    return {
      available: hasTTS(),
      isMuted: function () { return muted; },
      setMuted: function (value) {
        muted = value;
        if (value) stop();
      },
      speak: speak,
      stop: stop,
    };
  }

  /* ------------------------------------------------------------------------
     Speech recognition layer — browser-native only.
     Transcript segments are delivered through callbacks; the answer box
     stays the single source of truth submitted to the existing backend.
     ------------------------------------------------------------------------ */
  function createRecognition() {
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    var inst = null;
    var listening = false;
    var forceStop = false;
    var unavailable = false;
    var interimBuffer = "";
    var handlers = {};

    function ready() {
      return !!(SR && !unavailable);
    }

    function emit(name, data) {
      if (handlers[name]) handlers[name](data);
    }

    function handleError(code) {
      if (code === "not-allowed" || code === "service-not-allowed" ||
          code === "audio-capture" || code === "not-found" ||
          code === "service-not-found") {
        unavailable = true;
        emit("blocked", code);
        return;
      }
      emit("error", code);
    }

    function start() {
      if (!SR) {
        unavailable = true;
        emit("unavailable");
        return;
      }
      if (unavailable || listening) return;
      var rec;
      try {
        rec = new SR();
        rec.lang = "en-US";
        rec.interimResults = true;
        rec.continuous = false;
        rec.maxAlternatives = 1;
      } catch (e) {
        unavailable = true;
        emit("unavailable");
        return;
      }
      inst = rec;
      forceStop = false;
      interimBuffer = "";
      rec.onstart = function () {
        listening = true;
        emit("start");
      };
      rec.onresult = function (event) {
        var interim = "";
        for (var i = 0; i < event.results.length; i++) {
          var result = event.results[i];
          var text = "";
          if (result[0] && result[0].transcript) {
            text = result[0].transcript.trim();
          }
          if (result.isFinal) {
            if (text) emit("final", text);
          } else {
            interim = text;
          }
        }
        interimBuffer = interim;
        emit("interim", interim);
      };
      rec.onerror = function (event) {
        listening = false;
        interimBuffer = "";
        handleError(event.error || "");
      };
      rec.onend = function () {
        listening = false;
        var wasForced = forceStop;
        inst = null;
        emit("end", { forced: wasForced, interim: interimBuffer });
        interimBuffer = "";
      };
      try {
        rec.start();
      } catch (e) {
        inst = null;
        listening = false;
        unavailable = true;
        emit("unavailable");
      }
    }

    function stop() {
      if (!inst) return;
      forceStop = true;
      try {
        inst.stop();
      } catch (e) {
        listening = false;
        inst = null;
        emit("end", { forced: true, interim: "" });
      }
    }

    return {
      available: hasSTT(),
      ready: ready,
      start: start,
      stop: stop,
      on: function (name, fn) { handlers[name] = fn; },
    };
  }

  /* ------------------------------------------------------------------------
     Avatar controller.
     ------------------------------------------------------------------------ */
  function createAvatar(avatarEl, stateEl) {
    var names = ["idle", "thinking", "speaking", "listening", "processing"];
    function set(state) {
      var s = names.indexOf(state) >= 0 ? state : "idle";
      for (var i = 0; i < names.length; i++) {
        avatarEl.classList.remove("is-" + names[i]);
      }
      avatarEl.classList.add("is-" + s);
      if (stateEl) {
        stateEl.textContent = s.charAt(0).toUpperCase() + s.slice(1);
      }
    }
    set("idle");
    return { set: set };
  }

  /* ------------------------------------------------------------------------
     Answer box helpers.
     ------------------------------------------------------------------------ */
  function appendToAnswer(answer, text) {
    if (!answer || !text) return;
    var current = answer.value || "";
    var joined = (current.replace(/\s+$/, "") + " " + text).trim();
    answer.value = joined.length > ANSWER_MAX
      ? joined.slice(0, ANSWER_MAX)
      : joined;
  }

  function tailMentions(value, text) {
    if (!text) return true;
    var recent = value.slice(Math.max(0, value.length - text.length * 3));
    return recent.indexOf(text) !== -1;
  }

  /* ------------------------------------------------------------------------
     Live page.
     ------------------------------------------------------------------------ */
  function initLive() {
    var stage = document.querySelector("[data-riv-live]");
    if (!stage) return;

    var avatarEl = stage.querySelector("[data-avatar]");
    var stateEl = stage.querySelector("[data-avatar-state]");
    var questionEl = stage.querySelector("[data-question]");
    var answer = document.getElementById("answer");
    var form = document.getElementById("interview-answer-form");
    var timer = document.getElementById("interview-timer");
    var valueEl = document.getElementById("timer-value");

    var tts = createSpeech();
    var stt = createRecognition();
    var avatar = avatarEl
      ? createAvatar(avatarEl, stateEl)
      : { set: function () {} };

    var mode = storageGet();
    if (!isSupportedMode(mode)) mode = bestSupportedMode();

    var micArea = stage.querySelector("[data-mic]");
    var micBtn = stage.querySelector("[data-btn-mic]");
    var clearBtn = stage.querySelector("[data-btn-clear]");
    var micStatus = stage.querySelector("[data-mic-status]");
    var micInterim = stage.querySelector("[data-mic-interim]");
    var ttsControls = stage.querySelector("[data-tts-controls]");
    var ttsStatus = stage.querySelector("[data-tts-status]");
    var replayBtn = stage.querySelector("[data-btn-replay]");
    var muteBtn = stage.querySelector("[data-btn-mute]");
    var modeButtons = stage.querySelectorAll("[data-mode]");
    var modeIndicator = stage.querySelector("[data-mode-indicator]");
    var micBlocked = false;
    var thinkingTimer = null;

    function setTtsStatus(msg) {
      if (!ttsStatus) return;
      if (msg) {
        ttsStatus.textContent = msg;
        ttsStatus.hidden = false;
      } else {
        ttsStatus.textContent = "";
        ttsStatus.hidden = true;
      }
    }

    function setMicStatus(msg, kind) {
      if (!micStatus) return;
      micStatus.textContent = msg;
      micStatus.classList.remove("is-listening", "is-error");
      if (kind === "listening") micStatus.classList.add("is-listening");
      if (kind === "error") micStatus.classList.add("is-error");
    }

    function hideMicInterim() {
      if (micInterim) {
        micInterim.textContent = "";
        micInterim.hidden = true;
      }
    }

    function stopAll() {
      tts.stop();
      if (stt.ready()) stt.stop();
      setTtsStatus("");
    }

    function updateModeButtons() {
      Array.prototype.forEach.call(modeButtons, function (btn) {
        var active = btn.getAttribute("data-mode") === mode;
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
      });
      if (modeIndicator) modeIndicator.textContent = prettify(mode);
    }

    function speakQuestion(manual) {
      if (mode !== "voice-text" && mode !== "voice-voice") return;
      if (!questionEl) return;
      var text = (questionEl.textContent || "").trim();
      if (!text) return;
      if (!manual && tts.isMuted()) return;

      var hintTimer = window.setTimeout(function () {
        avatar.set("idle");
        setTtsStatus("Press \u201CReplay question\u201D to hear it again.");
      }, 2600);

      tts.speak(text, function () {
        window.clearTimeout(hintTimer);
        avatar.set("speaking");
        setTtsStatus("Speaking\u2026");
      }, function () {
        window.clearTimeout(hintTimer);
        avatar.set("idle");
        setTtsStatus("");
      }, function () {
        window.clearTimeout(hintTimer);
        avatar.set("idle");
        setTtsStatus(
          "Voice playback is unavailable \u2014 the question is shown above."
        );
      });
    }

    function applyMode() {
      var voice = mode === "voice-text" || mode === "voice-voice";
      stopAll();
      avatar.set("idle");
      if (thinkingTimer) {
        window.clearTimeout(thinkingTimer);
        thinkingTimer = null;
      }
      if (ttsControls) ttsControls.hidden = !voice;
      if (micArea) micArea.hidden = mode !== "voice-voice" || !stt.ready();
      if (micBtn) micBtn.disabled = !stt.ready();
      setMicStatus("Microphone off", "");
      hideMicInterim();
      updateModeButtons();

      if (voice) {
        avatar.set("thinking");
        window.setTimeout(function () {
          if (stage.querySelector("[data-question]")) speakQuestion(false);
        }, 350);
      } else {
        // Text -> Text: a brief "reading the question" cue so the interviewer
        // still reacts visually when a new question arrives (presentation
        // only — no interview logic is touched).
        avatar.set("thinking");
        thinkingTimer = window.setTimeout(function () {
          avatar.set("idle");
        }, 1300);
      }
    }

    function setMode(next) {
      if (!isSupportedMode(next) || next === mode) return;
      mode = next;
      storageSet(mode);
      applyMode();
    }

    /* --- speech controls ------------------------------------------------- */
    function wireSpeechControls() {
      if (replayBtn) {
        replayBtn.addEventListener("click", function () {
          speakQuestion(true);
        });
      }
      if (muteBtn) {
        muteBtn.addEventListener("click", function () {
          var next = !tts.isMuted();
          tts.setMuted(next);
          muteBtn.setAttribute("aria-pressed", next ? "true" : "false");
          muteBtn.textContent = next ? "Unmute voice" : "Mute voice";
          if (next) {
            avatar.set("idle");
            setTtsStatus("Voice muted.");
          } else {
            setTtsStatus("");
          }
        });
      }
    }

    /* --- microphone + transcript ------------------------------------------ */
    function wireMicrophone() {
      if (!micBtn) return;
      var recording = false;

      function setRecording(value) {
        recording = value;
        micBtn.textContent = value
          ? "\u{1F399} Stop recording"
          : "\u{1F399} Start microphone";
        micBtn.setAttribute("aria-label", value
          ? "Stop microphone"
          : "Start microphone");
      }

      micBtn.addEventListener("click", function () {
        if (!stt.ready()) {
          setMicStatus(
            "Voice input unavailable \u2014 type your answer instead.",
            "error"
          );
          return;
        }
        if (recording) {
          stt.stop();
        } else {
          stt.start();
        }
      });

      stt.on("start", function () {
        setRecording(true);
        avatar.set("listening");
        setMicStatus("Listening\u2026 speak now.", "listening");
        if (micInterim) micInterim.hidden = false;
      });

      stt.on("interim", function (text) {
        if (micInterim) {
          micInterim.textContent = text ? "\u201C" + text + "\u201D" : "";
        }
      });

      stt.on("final", function (text) {
        appendToAnswer(answer, text);
      });

      stt.on("end", function (info) {
        setRecording(false);
        if (info && info.forced && info.interim) {
          var draft = info.interim;
          if (answer && !tailMentions(answer.value || "", draft)) {
            appendToAnswer(answer, draft);
          }
        }
        hideMicInterim();
        avatar.set("processing");
        setMicStatus(
          "Microphone off \u2014 review and edit the transcript, then submit.",
          ""
        );
        window.setTimeout(function () { avatar.set("idle"); }, 700);
      });

      stt.on("blocked", function (code) {
        setRecording(false);
        micBlocked = true;
        avatar.set("idle");
        hideMicInterim();
        if (micBtn) micBtn.disabled = true;
        if (micArea) micArea.hidden = true;
        if (code === "not-allowed" || code === "service-not-allowed") {
          setMicStatus(
            "Microphone permission was denied \u2014 type your answer instead.",
            "error"
          );
        } else {
          setMicStatus(
            "No microphone detected \u2014 type your answer instead.",
            "error"
          );
        }
      });

      stt.on("error", function (code) {
        setRecording(false);
        avatar.set("idle");
        hideMicInterim();
        var msg = "Voice input stopped \u2014 try again or type your answer.";
        if (code === "no-speech") {
          msg = "No speech detected \u2014 try again or type your answer.";
        } else if (code === "network") {
          msg = "The speech service had a network error \u2014 type your answer instead.";
        } else if (code === "aborted") {
          msg = "Recording stopped.";
        }
        setMicStatus(msg, "error");
        window.setTimeout(function () {
          if (!recording) setMicStatus("Microphone off", "");
        }, 4000);
      });

      stt.on("unavailable", function () {
        setRecording(false);
        micBlocked = true;
        avatar.set("idle");
        if (micBtn) micBtn.disabled = true;
        if (micArea) micArea.hidden = true;
        setMicStatus("Voice input unavailable in this browser.", "error");
      });

      if (clearBtn && answer) {
        clearBtn.addEventListener("click", function () {
          answer.value = "";
          answer.focus();
        });
      }
    }

    /* --- mode switcher (live) ------------------------------------------- */
    function wireModeSwitch() {
      Array.prototype.forEach.call(modeButtons, function (btn) {
        btn.addEventListener("click", function () {
          setMode(btn.getAttribute("data-mode"));
        });
      });
    }

    /* --- timer: server-enforced, mirrored client-side --------------------- */
    var submitted = false;

    function render(seconds) {
      var safe = Math.max(seconds, 0);
      var minutes = Math.floor(safe / 60);
      var secs = safe % 60;
      if (valueEl) {
        valueEl.textContent =
          (minutes < 10 ? "0" + minutes : minutes) +
          ":" +
          (secs < 10 ? "0" + secs : secs);
      }
      if (timer) {
        timer.classList.toggle("timer-low", safe < 60 && safe > 0);
        timer.classList.toggle("timer-expired", safe === 0);
      }
    }

    function flushAndFreeze() {
      if (stt.ready()) stt.stop();
      tts.stop();
      if (answer) answer.readOnly = true;
      var btn = document.getElementById("answer-submit");
      if (btn) btn.disabled = true;
    }

    function submitOnce() {
      if (submitted) return;
      submitted = true;
      if (form) {
        form.submit();
      } else {
        window.location.reload();
      }
    }

    function initTimer() {
      if (!timer || !valueEl) return;
      var remaining = parseInt(
        timer.getAttribute("data-seconds-remaining"), 10
      );
      if (isNaN(remaining)) return;
      render(remaining);

      // When the server re-renders this live view it does so with a preserved
      // draft if the previous evaluation was rate-limited or otherwise failed
      // mid-turn. By then the timer has nearly always reached zero, and
      // auto-submitting again would immediately re-fire the same evaluation —
      // an automatic duplicate-submission loop that burns quota and keeps
      // tripping the rate limiter. Instead the draft stays editable and the
      // user submits manually, matching the recovery message.
      var preserved =
        stage.getAttribute("data-answer-preserved") === "1";

      var tickHandle = null;

      function expiry() {
        clearInterval(tickHandle);
        if (!preserved) {
          flushAndFreeze();
          submitOnce();
        }
      }

      if (remaining <= 0) {
        expiry();
        return;
      }
      tickHandle = setInterval(function () {
        remaining -= 1;
        render(remaining);
        if (remaining <= 0) expiry();
      }, 1000);
    }

    if (form) {
      form.addEventListener("submit", function (event) {
        if (submitted) {
          event.preventDefault();
          return;
        }
        if (stt.ready()) stt.stop();
        tts.stop();
        if (answer && !answer.value.trim() && !window.confirm(
          "Submit an empty answer? The interview will move to the next " +
          "question without one."
        )) {
          event.preventDefault();
          return;
        }
        submitted = true;
        var btn = document.getElementById("answer-submit");
        if (btn) btn.disabled = true;
        avatar.set("processing");
      });
    }

    window.addEventListener("pagehide", function () {
      tts.stop();
      if (stt.ready()) stt.stop();
    });
    window.addEventListener("beforeunload", function () {
      tts.stop();
      if (stt.ready()) stt.stop();
    });

    wireSpeechControls();
    wireMicrophone();
    wireModeSwitch();
    applyMode();
    initTimer();
  }

  /* ------------------------------------------------------------------------
     Boot.
     ------------------------------------------------------------------------ */
  function boot() {
    if (document.querySelector("[data-mode-card]")) initConfig();
    if (document.querySelector("[data-riv-live]")) initLive();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  /* Warm up voices early so the voice picker is populated on first play. */
  if (hasTTS()) {
    try {
      window.speechSynthesis.getVoices();
    } catch (e) {
      /* ignore */
    }
    if ("onvoiceschanged" in window.speechSynthesis) {
      window.speechSynthesis.onvoiceschanged = function () {
        try {
          window.speechSynthesis.getVoices();
        } catch (e) {
          /* ignore */
        }
      };
    }
  }
})();