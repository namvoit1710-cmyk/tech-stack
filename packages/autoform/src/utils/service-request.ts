async function serviceRequest(
  service: any,
  method: string,
  url: string,
  body?: unknown,
  headers?: Record<string, string>
): Promise<unknown> {
  switch (method) {
    case "GET":    return await service.fetch(url);
    case "POST":   return await service.post(url, body, headers);
    case "PUT":    return await service.put(url, body, headers);
    case "PATCH":  return await service.patch(url, body, headers);
    case "DELETE": return await service.delete(url, headers);
    default:       throw new Error(`Unsupported method: ${method}`);
  }
}

export default serviceRequest;