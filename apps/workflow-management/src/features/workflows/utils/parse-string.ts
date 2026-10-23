export function snakeToCamel(str: string): string {
  return str.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

export function convertKeys(obj: any): any {
  if (Array.isArray(obj)) return obj.map(convertKeys);
  if (obj !== null && typeof obj === "object") {
    return Object.fromEntries(
      Object.entries(obj).map(([key, value]) => [
        snakeToCamel(key),
        convertKeys(value),
      ])
    );
  }
  return obj;
}