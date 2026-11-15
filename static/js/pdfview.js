import * as pdfjsLib from "/static/vendor/pdf.min.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdf.worker.min.mjs";

let token = 0;

window.PdfView = {
  async render(container, buffer) {
    const mine = ++token;
    const pdf = await pdfjsLib.getDocument({ data: buffer }).promise;
    const pages = document.createDocumentFragment();
    const width = container.clientWidth - 2;
    for (let n = 1; n <= Math.min(pdf.numPages, 6); n++) {
      const page = await pdf.getPage(n);
      const base = page.getViewport({ scale: 1 });
      const scale = (width / base.width) * (window.devicePixelRatio || 1);
      const viewport = page.getViewport({ scale });
      const canvas = document.createElement("canvas");
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = "100%";
      canvas.className = "bg-white rounded shadow-sm mb-3 block";
      await page.render({ canvasContext: canvas.getContext("2d"), viewport, canvas }).promise;
      pages.appendChild(canvas);
    }
    if (mine !== token) return null;
    container.replaceChildren(pages);
    return pdf.numPages;
  },
};
window.dispatchEvent(new Event("pdfview-ready"));
