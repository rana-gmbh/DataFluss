// Windows only (main.rs, database_window). WebView2 leaves the contents of a chosen file out of a
// request to the app:// scheme: the shell got the form's fields and an empty file, so every upload
// failed ("Diese Datei ist kein gültiges Bild."). Chromium keeps a file in a form body as a reference
// that the scheme handler never reads (https://github.com/MicrosoftEdge/WebView2Feedback/issues/2162).
// Bytes do arrive, so a form body with files is turned into its multipart bytes before it is sent:
// for HTMX (XMLHttpRequest), for the pages' own fetch calls, and for plain form posts.
(() => {
  if (window.__dataflussUploadBytes) return;
  window.__dataflussUploadBytes = true;

  const hasFile = (body) => body instanceof FormData && [...body.values()].some((value) => value instanceof Blob);

  // A Request encodes FormData exactly as the browser would send it, boundary included.
  async function encode(data) {
    const request = new Request(location.href, { method: "POST", body: data });
    return { type: request.headers.get("content-type"), bytes: await request.arrayBuffer() };
  }

  const fetchOriginal = window.fetch;
  window.fetch = async function (input, init) {
    if (init && hasFile(init.body)) {
      const { type, bytes } = await encode(init.body);
      const headers = new Headers(init.headers);
      headers.set("content-type", type);
      init = { ...init, body: bytes, headers };
    }
    return fetchOriginal.call(this, input, init);
  };

  const sendOriginal = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (body) {
    if (!hasFile(body)) return sendOriginal.call(this, body);
    encode(body).then(
      ({ type, bytes }) => {
        this.setRequestHeader("Content-Type", type);
        sendOriginal.call(this, bytes);
      },
      () => sendOriginal.call(this, body),
    );
  };

  // A plain form post with a file: sent here, then shown as the browser would have. Listening on
  // window, last, so the page's own handlers (HTMX, the side panel, the session check) come first
  // and a form they took over is left alone.
  async function submitWithBytes(form, submitter) {
    const { type, bytes } = await encode(new FormData(form, submitter));
    // Accept: text/html makes the shell answer a redirect with a page that goes there.
    const response = await fetchOriginal(form.action, {
      method: "POST",
      body: bytes,
      headers: { "content-type": type, accept: "text/html" },
    });
    if (response.status === 204) return; // a download, saved by the shell; the page stays
    const html = await response.text();
    const target = html.match(/<meta http-equiv=refresh content="0;url=([^"]*)">/i);
    if (target) {
      location.assign(target[1]);
      return;
    }
    history.replaceState(null, "", form.action);
    document.open();
    document.write(html);
    document.close();
    listen(); // document.open() removed the window's listeners
  }

  function listen() {
    window.addEventListener("submit", (event) => {
      const form = event.target;
      if (event.defaultPrevented || !(form instanceof HTMLFormElement)) return;
      if (form.method !== "post" || form.target || form.enctype !== "multipart/form-data") return;
      if (!hasFile(new FormData(form, event.submitter))) return;
      event.preventDefault();
      submitWithBytes(form, event.submitter).catch(() => {
        delete form.dataset.sent;
        alert("Das Formular konnte nicht gesendet werden.");
      });
    });
  }
  listen();
})();
