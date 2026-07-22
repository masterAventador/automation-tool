/**
 * Node 26 can bridge WebdriverIO's Undici request through Node's global
 * dispatcher. An explicit Content-Length from the package-level Undici is not
 * valid across that bridge, while fetch can calculate the same value safely.
 */
export function withoutExplicitContentLength(requestOptions: RequestInit): RequestInit {
  const { headers } = requestOptions;
  if (headers instanceof Headers) {
    headers.delete("content-length");
  } else if (Array.isArray(headers)) {
    for (let index = headers.length - 1; index >= 0; index -= 1) {
      if (headers[index]?.[0].toLowerCase() === "content-length") {
        headers.splice(index, 1);
      }
    }
  } else if (headers) {
    for (const name of Object.keys(headers)) {
      if (name.toLowerCase() === "content-length") {
        delete headers[name];
      }
    }
  }
  return requestOptions;
}
