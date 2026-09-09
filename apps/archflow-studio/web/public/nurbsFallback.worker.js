// This is a classic worker so it can reuse the same public rhino3dm runtime
// as Three's loader without bundling its Node-only compatibility branches.
importScripts("/rhino3dm/rhino3dm.js");

const EDGE_SAMPLES = 24;

function multiply(left, right) {
  const product = Array(16).fill(0);
  for (let row = 0; row < 4; row += 1) for (let column = 0; column < 4; column += 1) {
    for (let offset = 0; offset < 4; offset += 1) product[row * 4 + column] += left[row * 4 + offset] * right[offset * 4 + column];
  }
  return product;
}

function matrixOf(transform) {
  return [transform.m00, transform.m01, transform.m02, transform.m03, transform.m10, transform.m11, transform.m12, transform.m13, transform.m20, transform.m21, transform.m22, transform.m23, transform.m30, transform.m31, transform.m32, transform.m33];
}

function through(matrix, point) {
  const [x, y, z] = point;
  return [matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3], matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7], matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11]];
}

function pathsForBrep(brep, matrix, attributes, into) {
  const edges = brep.edges();
  try {
    for (let index = 0; index < edges.count; index += 1) {
      const edge = edges.get(index);
      try {
        const domain = edge.domain, positions = [];
        if (!Array.isArray(domain) || domain.length !== 2) continue;
        let previous = null;
        for (let sample = 0; sample <= EDGE_SAMPLES; sample += 1) {
          const point = edge.pointAt(domain[0] + (domain[1] - domain[0]) * sample / EDGE_SAMPLES);
          if (!Array.isArray(point) || point.length < 3 || point.some((value) => !Number.isFinite(value))) { previous = null; break; }
          const transformed = through(matrix, point);
          if (previous !== null) positions.push(...previous, ...transformed);
          previous = transformed;
        }
        if (positions.length > 0) into.push({ positions: new Float32Array(positions), attributes });
      } finally { edge.delete(); }
    }
  } finally { edges.delete(); }
}

function loopsForFace(brep, face, matrix) {
  if (!face.isPlanar()) return null;
  const edges = brep.edges(), loops = [];
  try {
    for (let loopIndex = 0; loopIndex < face.loops.count; loopIndex += 1) {
      const loop = face.loops.get(loopIndex), points = [];
      try {
        for (let trimIndex = 0; trimIndex < loop.trims.count; trimIndex += 1) {
          const trim = loop.trims.get(trimIndex);
          try {
            if (trim.edgeIndex < 0) return null;
            const edge = edges.get(trim.edgeIndex), domain = edge.domain;
            try {
              for (let sample = 0; sample <= EDGE_SAMPLES; sample += 1) {
                const fraction = trim.isReversed ? 1 - sample / EDGE_SAMPLES : sample / EDGE_SAMPLES;
                const point = edge.pointAt(domain[0] + (domain[1] - domain[0]) * fraction);
                if (!Array.isArray(point) || point.length < 3 || point.some((value) => !Number.isFinite(value))) return null;
                if (trimIndex > 0 && sample === 0) continue;
                points.push(through(matrix, point));
              }
            } finally { edge.delete(); }
          } finally { trim.delete(); }
        }
      } finally { loop.delete(); }
      if (points.length < 3) return null;
      loops.push(points);
    }
  } finally { edges.delete(); }
  return loops.length > 0 ? loops : null;
}

function facesForBrep(brep, matrix, attributes, into) {
  const faces = brep.faces();
  let unsupportedFaces = 0;
  try {
    for (let index = 0; index < faces.count; index += 1) {
      const face = faces.get(index);
      try {
        const loops = loopsForFace(brep, face, matrix);
        if (loops !== null) into.push({ loops, attributes });
        else unsupportedFaces += 1;
      } finally { face.delete(); }
    }
  } finally { faces.delete(); }
  return unsupportedFaces;
}

function fallbackPatches(bytes, rhino) {
  const document = rhino.File3dm.fromByteArray(bytes), objects = document.objects(), definitions = document.instanceDefinitions();
  const objectById = new Map(), definitionMembers = new Map(), layersByIndex = new Map(), layersById = new Map(), paths = [], faces = [];
  let unsupportedFaces = 0;
  try {
    const layers = document.layers();
    for (let index = 0; index < layers.count; index += 1) {
      const layer = layers.get(index);
      try {
        const saved = { id: layer.id, parentId: layer.parentLayerId, visible: layer.visible };
        layersByIndex.set(layer.index, saved);
        layersById.set(saved.id, saved);
      } finally { layer.delete(); }
    }
    const layerVisible = (index) => {
      const seen = new Set();
      let layer = layersByIndex.get(index);
      while (layer && !seen.has(layer.id)) {
        if (layer.visible === false) return false;
        seen.add(layer.id);
        layer = layersById.get(layer.parentId);
      }
      return true;
    };
    for (let index = 0; index < definitions.count; index += 1) {
      const definition = definitions.get(index);
      try { definitionMembers.set(definition.id, Array.from(definition.getObjectIds())); } finally { definition.delete(); }
    }
    for (let index = 0; index < objects.count; index += 1) {
      const object = objects.get(index), attributes = object.attributes(), geometry = object.geometry();
      try {
        objectById.set(attributes.id, { geometry, isDefinitionObject: attributes.isInstanceDefinitionObject, attributes: { name: attributes.name, visible: attributes.visible, layerIndex: attributes.layerIndex, userStrings: attributes.getUserStrings() } });
      } finally { attributes.delete(); object.delete(); }
    }
    const appendMember = (id, matrix, seen) => {
      const member = objectById.get(id);
      // An instance is a visibility gate for every nested member. Skip its
      // branch before sampling so hidden geometry cannot affect display bounds
      // or the count of faces that are only recoverable as edges.
      if (!member || seen.has(id) || member.attributes.visible === false || !layerVisible(member.attributes.layerIndex)) return;
      seen.add(id);
      if (member.geometry.objectType === rhino.ObjectType.Brep) { pathsForBrep(member.geometry, matrix, member.attributes, paths); unsupportedFaces += facesForBrep(member.geometry, matrix, member.attributes, faces); }
      if (member.geometry.objectType === rhino.ObjectType.Extrusion) {
        const brep = member.geometry.toBrep(false);
        try { pathsForBrep(brep, matrix, member.attributes, paths); unsupportedFaces += facesForBrep(brep, matrix, member.attributes, faces); } finally { brep.delete(); }
      }
      if (member.geometry.objectType === rhino.ObjectType.InstanceReference) appendDefinition(member.geometry.parentIdefId, multiply(matrix, matrixOf(member.geometry.xform)), seen);
    };
    const appendDefinition = (id, matrix, seen) => { for (const member of definitionMembers.get(id) || []) appendMember(member, matrix, new Set(seen)); };
    for (const [id, member] of objectById) if (!member.isDefinitionObject) appendMember(id, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], new Set());
  } finally {
    for (const member of objectById.values()) member.geometry.delete();
    document.delete();
  }
  return { paths, faces, unsupportedFaces };
}

self.onmessage = async (event) => {
  try {
    const rhino = await rhino3dm({ locateFile: () => "/rhino3dm/rhino3dm.wasm" });
    const result = fallbackPatches(new Uint8Array(event.data), rhino);
    self.postMessage(result, result.paths.map((path) => path.positions.buffer));
  } catch (error) { self.postMessage({ error: error instanceof Error ? error.message : String(error) }); }
};
