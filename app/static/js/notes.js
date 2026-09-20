/* InterviewIQ — Personal Notes saved-state indicator.
 * Detects unsaved edits to the note textarea and flips the Saved / Unsaved /
 * "No note yet" status label to give the user immediate feedback before they
 * press Save. Strict CSP: no inline handlers, no eval — everything runs in
 * this self-contained module.
 */
(function () {
  'use strict';
  var input = document.getElementById('note-content');
  if (!input) return;

  var el = document.getElementById('personal-note-state');
  if (!el) return;

  var savedValue = (input.getAttribute('data-saved') || '').trim();
  var savedText = el.getAttribute('data-state-saved') || 'Saved';
  var unsavedText = el.getAttribute('data-state-unsaved') || 'Unsaved';
  var emptyText = el.getAttribute('data-state-empty') || 'No note yet';

  function sync() {
    var text = input.value.trim();
    if (text === '') {
      el.textContent = emptyText;
      el.classList.remove('is-unsaved');
      return;
    }
    var isSaved = text === savedValue;
    el.textContent = isSaved ? savedText : unsavedText;
    if (isSaved) {
      el.classList.remove('is-unsaved');
    } else {
      el.classList.add('is-unsaved');
    }
  }

  input.addEventListener('input', sync);
  sync();
})();