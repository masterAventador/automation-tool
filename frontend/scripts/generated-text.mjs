export function normalizeGeneratedText(value) {
  return value.replace(/\r\n?/g, "\n");
}
