import * as pdfjs from "/vendor/pdfjs/pdf.mjs";

pdfjs.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.mjs";

const state = {
  document: null,
  page: 1,
  scale: 1.2,
  renderTask: null,
};

const element = (id) => document.getElementById(id);

async function renderPage() {
  if (!state.document) return;
  if (state.renderTask) {
    state.renderTask.cancel();
    state.renderTask = null;
  }
  const page = await state.document.getPage(state.page);
  const viewport = page.getViewport({ scale: state.scale });
  const ratio = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  const canvas = element("pdfViewerCanvas");
  const context = canvas.getContext("2d", { alpha: false });
  canvas.width = Math.floor(viewport.width * ratio);
  canvas.height = Math.floor(viewport.height * ratio);
  canvas.style.width = `${Math.floor(viewport.width)}px`;
  canvas.style.height = `${Math.floor(viewport.height)}px`;

  const textLayer = element("pdfViewerTextLayer");
  textLayer.replaceChildren();
  textLayer.style.width = `${Math.floor(viewport.width)}px`;
  textLayer.style.height = `${Math.floor(viewport.height)}px`;

  const renderTask = page.render({
    canvasContext: context,
    viewport,
    transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0],
  });
  state.renderTask = renderTask;
  try {
    await renderTask.promise;
  } catch (error) {
    if (error?.name !== "RenderingCancelledException") throw error;
    return;
  } finally {
    if (state.renderTask === renderTask) state.renderTask = null;
  }

  const textContent = await page.getTextContent();
  const layer = new pdfjs.TextLayer({ textContentSource: textContent, container: textLayer, viewport });
  await layer.render();
  element("pdfViewerPage").textContent = `${state.page} / ${state.document.numPages}`;
  element("pdfViewerPrevious").disabled = state.page <= 1;
  element("pdfViewerNext").disabled = state.page >= state.document.numPages;
  element("pdfViewerZoom").textContent = `${Math.round(state.scale * 100)}%`;
}

async function openPdf(url, name = "PDF", page = 1) {
  const dialog = element("pdfViewerDialog");
  if (!dialog) {
    window.open(url, "_blank", "noopener,noreferrer");
    return;
  }
  element("pdfViewerTitle").textContent = name || "PDF";
  element("pdfViewerStage").classList.add("is-loading");
  if (!dialog.open) dialog.showModal();
  try {
    const previous = state.document;
    state.document = await pdfjs.getDocument({ url }).promise;
    if (state.renderTask) state.renderTask.cancel();
    await previous?.loadingTask?.destroy();
    state.page = Math.max(1, Math.min(Number(page) || 1, state.document.numPages));
    await renderPage();
  } catch (error) {
    element("pdfViewerTextLayer").textContent = `无法打开 PDF：${error.message || error}`;
  } finally {
    element("pdfViewerStage").classList.remove("is-loading");
  }
}

function closePdf() {
  const dialog = element("pdfViewerDialog");
  if (dialog?.open) dialog.close();
}

window.ScanSciPdfViewer = { open: openPdf, close: closePdf };

window.addEventListener("DOMContentLoaded", () => {
  element("pdfViewerClose")?.addEventListener("click", closePdf);
  element("pdfViewerPrevious")?.addEventListener("click", async () => {
    if (state.page <= 1) return;
    state.page -= 1;
    await renderPage();
  });
  element("pdfViewerNext")?.addEventListener("click", async () => {
    if (!state.document || state.page >= state.document.numPages) return;
    state.page += 1;
    await renderPage();
  });
  element("pdfViewerZoomOut")?.addEventListener("click", async () => {
    state.scale = Math.max(0.6, state.scale - 0.2);
    await renderPage();
  });
  element("pdfViewerZoomIn")?.addEventListener("click", async () => {
    state.scale = Math.min(2.8, state.scale + 0.2);
    await renderPage();
  });
  element("pdfViewerDialog")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closePdf();
  });
});
