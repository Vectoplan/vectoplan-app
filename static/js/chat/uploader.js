import { listAttachments } from "./render.js";

export function wireFileInput(inputEl, listEl) {
  inputEl.addEventListener("change", () => listAttachments(listEl, inputEl.files));
}

export function collectAttachmentMeta(files) {
  return [...(files || [])].map(f => ({ name: f.name, size: f.size, type: f.type }));
}
