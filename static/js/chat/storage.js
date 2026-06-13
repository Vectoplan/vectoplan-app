export function loadAuth() {
  return {
    baseUrl: localStorage.getItem("chat.baseUrl") || "",
    clientId: localStorage.getItem("chat.clientId") || "",
    apiKey: localStorage.getItem("chat.apiKey") || ""
  };
}
export function saveAuth({ baseUrl, clientId, apiKey }) {
  if (baseUrl !== undefined) localStorage.setItem("chat.baseUrl", baseUrl);
  if (clientId !== undefined) localStorage.setItem("chat.clientId", clientId);
  if (apiKey !== undefined) localStorage.setItem("chat.apiKey", apiKey);
}
export function clearAuth() {
  ["chat.baseUrl","chat.clientId","chat.apiKey"].forEach(k => localStorage.removeItem(k));
}
