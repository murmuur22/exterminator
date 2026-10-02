'use strict';
const form = document.querySelector('#report-form');
const sendButton = form.querySelector('button[type="submit"]');
const error = document.querySelector('#send-error');
let sending = false;

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (sending) return;
  sending = true;
  sendButton.disabled = true;
  sendButton.textContent = 'Sending…';
  error.hidden = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch('/api/submit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Exterminator': '1'},
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
      signal: controller.signal
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not confirm delivery. Please try again.');
    if (result.ok !== true) throw new Error('Could not confirm delivery. Please try again.');
    form.reset();
    document.querySelector('#form-section').hidden = true;
    document.querySelector('#confirmation').hidden = false;
    document.querySelector('#success-heading').focus();
  } catch (problem) {
    error.textContent = problem instanceof TypeError || problem.name === 'AbortError' || problem instanceof SyntaxError
      ? 'Could not confirm delivery. Your form is still here. Check the connection before trying again; a retry could create a duplicate if the first report arrived.'
      : problem.message;
    error.hidden = false;
  } finally {
    clearTimeout(timeout);
    sending = false;
    sendButton.disabled = false;
    sendButton.textContent = 'Send report';
  }
});

document.querySelector('#another').addEventListener('click', () => {
  document.querySelector('#confirmation').hidden = true;
  document.querySelector('#form-section').hidden = false;
  error.hidden = true;
  document.querySelector('#title').focus();
});
