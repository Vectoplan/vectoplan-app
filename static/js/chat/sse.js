export function startStream(url, headers, onChunk, onDone, onError) {
  // Platzhalter für SSE/WebSocket
  onError?.(new Error("Streaming noch nicht implementiert"));
  return () => {};
}
