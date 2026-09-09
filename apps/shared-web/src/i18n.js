/** Fixed UI text, using the original Studio interpolation rules. */
export function translateMessage(catalog, key, parameters) {
  const message = catalog[key] ?? key;
  if (parameters === undefined) return message;
  return message.replace(/\{([A-Za-z][\w.-]*)\}/g, (placeholder, name) =>
    Object.prototype.hasOwnProperty.call(parameters, name) ? String(parameters[name]) : placeholder,
  );
}
