/* InterviewIQ — Real Interview timer (Stage 3, blueprint H.6):
   - counts down from a server-provided number of seconds
   - turns red under 60 seconds
   - at zero auto-submits the pending answer as-is (or finalizes when empty)
   - prevents double submission of the answer form
   Degrades gracefully: without JS the form simply submits manually. */
(function () {
  "use strict";

  var timer = document.getElementById("interview-timer");
  var form = document.getElementById("interview-answer-form");
  var valueEl = document.getElementById("timer-value");
  if (!timer || !valueEl) return;

  var remaining = parseInt(timer.getAttribute("data-seconds-remaining"), 10);
  if (isNaN(remaining)) return;

  var submitted = false;
  var tickHandle = null;

  function render(seconds) {
    var safe = Math.max(seconds, 0);
    var minutes = Math.floor(safe / 60);
    var secs = safe % 60;
    valueEl.textContent =
      (minutes < 10 ? "0" + minutes : minutes) +
      ":" +
      (secs < 10 ? "0" + secs : secs);
    timer.classList.toggle("timer-low", safe < 60 && safe > 0);
    timer.classList.toggle("timer-expired", safe === 0);
  }

  function submitOnce() {
    if (submitted) return;
    submitted = true;
    var textarea = document.getElementById("answer");
    var button = document.getElementById("answer-submit");
    if (button) button.disabled = true;
    if (textarea) textarea.readOnly = true;
    if (form) form.submit();
    else window.location.reload();
  }

  function onTick() {
    remaining -= 1;
    render(remaining);
    if (remaining <= 0) {
      clearInterval(tickHandle);
      // Timer expiry: submit whatever is typed as-is. An empty box still
      // posts so the server can finalize the session.
      submitOnce();
    }
  }

  render(remaining);
  if (remaining <= 0) {
    submitOnce();
    return;
  }

  tickHandle = setInterval(onTick, 1000);

  if (form) {
    form.addEventListener("submit", function () {
      if (submitted) {
        return;
      }
      submitted = true;
      var button = document.getElementById("answer-submit");
      if (button) button.disabled = true;
      if (!document.getElementById("answer").value.trim() &&
          !window.confirm(
            "Submit an empty answer? The interview will move to the next " +
            "question without one."
          )) {
        submitted = false;
        if (button) button.disabled = false;
        event.preventDefault();
      }
    });
  }
})();
