const NODE_KIND_ORDER = { root: 0, category: 1, asset: 2, external: 3 };

function text(value) {
  return String(value ?? '').trim();
}

function compareText(left, right) {
  return text(left).localeCompare(text(right), 'zh-CN');
}

function slug(value, fallback = 'unknown') {
  const normalized = text(value).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return normalized || fallback;
}

function externalNodeId(edge) {
  const identity = edge.remote_chassis_id
    || edge.remote_system_name
    || (edge.remote_management_addresses || [])[0]
    || edge.id;
  const port = edge.remote_port_id || edge.remote_port_description || 'port';
  return `external:${slug(identity)}:${slug(port)}`;
}

function protocolLabel(protocols) {
  const hasLldp = protocols.some((value) => value.includes('lldp'));
  const hasCdp = protocols.some((value) => value.includes('cdp'));
  if (hasLldp && hasCdp) return 'LLDP/CDP';
  if (hasCdp) return 'CDP';
  if (hasLldp) return 'LLDP';
  return '物理发现';
}

function statusLabel(status) {
  return status === 'current' ? '当前' : status === 'stale' ? '已过期' : '未知';
}

function deviceNodeId(deviceId, nodeIds) {
  const candidate = `networks:${text(deviceId)}`;
  return nodeIds.has(candidate) ? candidate : null;
}

function mergePhysicalEdge(existing, incoming) {
  const protocols = [...new Set([...existing.protocols, ...incoming.protocols])].sort(compareText);
  const isReciprocal = existing.observationCount + incoming.observationCount > 1;
  return {
    ...existing,
    protocols,
    protocolLabel: protocolLabel(protocols),
    evidenceDirection: isReciprocal || existing.evidenceDirection === 'bidirectional'
      || incoming.evidenceDirection === 'bidirectional' ? 'bidirectional' : incoming.evidenceDirection,
    status: existing.status === 'current' || incoming.status === 'current' ? 'current' : incoming.status,
    statusLabel: statusLabel(existing.status === 'current' || incoming.status === 'current' ? 'current' : incoming.status),
    confidence: Math.max(existing.confidence, incoming.confidence),
    lastSeenAt: [existing.lastSeenAt, incoming.lastSeenAt].filter(Boolean).sort().at(-1) || null,
    observationCount: existing.observationCount + incoming.observationCount,
  };
}

export function normalizeTopology(snapshot = {}) {
  const sourceNodes = Array.isArray(snapshot.nodes) ? snapshot.nodes : [];
  const baseNodes = sourceNodes
    .filter((node) => node && text(node.id))
    .map((node) => ({ ...node, id: text(node.id), kind: text(node.kind) || 'asset' }));
  baseNodes.sort((left, right) => (
    (NODE_KIND_ORDER[left.kind] ?? 9) - (NODE_KIND_ORDER[right.kind] ?? 9)
    || compareText(left.label, right.label)
    || compareText(left.id, right.id)
  ));
  const nodeIds = new Set(baseNodes.map((node) => node.id));
  const nodeIndex = new Map(baseNodes.map((node, index) => [node.id, index]));

  const logicalEdges = (Array.isArray(snapshot.logical_edges) ? snapshot.logical_edges : [])
    .filter((edge) => edge && nodeIds.has(text(edge.source)) && nodeIds.has(text(edge.target))
      && text(edge.source) !== text(edge.target))
    .map((edge) => ({
      id: text(edge.id), source: text(edge.source), target: text(edge.target),
      relationship: text(edge.relationship) || 'logical_membership',
    }))
    .sort((left, right) => (
      nodeIndex.get(left.source) - nodeIndex.get(right.source)
      || nodeIndex.get(left.target) - nodeIndex.get(right.target)
      || compareText(left.id, right.id)
    ));

  const interfaces = new Map(
    (Array.isArray(snapshot.interfaces) ? snapshot.interfaces : [])
      .filter((row) => row && text(row.id))
      .map((row) => [text(row.id), row]),
  );
  const externalNodes = new Map();
  const physicalByEndpoints = new Map();
  const sourceEdges = (Array.isArray(snapshot.physical_edges) ? snapshot.physical_edges : [])
    .filter((edge) => edge && edge.kind === 'physical_discovered')
    .sort((left, right) => compareText(left.id, right.id));

  for (const edge of sourceEdges) {
    const localNode = deviceNodeId(edge.local_device_id, nodeIds);
    if (!localNode) continue;
    let remoteNode = deviceNodeId(edge.remote_device_id, nodeIds);
    if (!remoteNode) {
      remoteNode = externalNodeId(edge);
      if (!externalNodes.has(remoteNode)) {
        externalNodes.set(remoteNode, {
          id: remoteNode,
          kind: 'external',
          label: text(edge.remote_system_name) || text(edge.remote_chassis_id) || '未解析邻居',
          subtitle: text(edge.remote_port_id) || text(edge.remote_management_addresses?.[0]),
          status: edge.status === 'stale' ? 'stale' : 'unknown',
          url: '',
          parent_id: null,
        });
      }
    }
    if (localNode === remoteNode) continue;

    const localInterface = interfaces.get(text(edge.local_interface_id));
    const remoteInterface = interfaces.get(text(edge.remote_interface_id));
    const isExternal = remoteNode.startsWith('external:');
    const reverseResolved = !isExternal && compareText(localNode, remoteNode) > 0;
    const source = reverseResolved ? remoteNode : localNode;
    const target = reverseResolved ? localNode : remoteNode;
    const protocols = [...new Set(Array.isArray(edge.protocols) ? edge.protocols.map(text) : [])].sort(compareText);
    const currentStatus = text(edge.status) || 'unknown';
    const normalized = {
      id: `physical:${source}:${target}`,
      kind: 'physical_discovered',
      source,
      target,
      localInterfaceLabel: reverseResolved
        ? (text(remoteInterface?.name) || text(edge.remote_port_id))
        : (text(localInterface?.name) || text(localInterface?.stable_key)),
      remoteInterfaceLabel: reverseResolved
        ? (text(localInterface?.name) || text(localInterface?.stable_key))
        : (text(remoteInterface?.name) || text(edge.remote_port_id) || text(edge.remote_port_description)),
      protocols,
      protocolLabel: protocolLabel(protocols),
      evidenceDirection: text(edge.evidence_direction) || 'unknown',
      resolutionStatus: text(edge.resolution_status) || (isExternal ? 'unresolved' : 'resolved'),
      status: currentStatus,
      statusLabel: statusLabel(currentStatus),
      speedBps: edge.speed_bps ?? localInterface?.speed_bps ?? null,
      vlanIds: Array.isArray(edge.vlan_ids) ? [...edge.vlan_ids] : (localInterface?.vlan_ids || []),
      confidence: Number(edge.confidence || 0),
      lastSeenAt: edge.last_seen_at || null,
      observationCount: 1,
    };
    const key = `${source}|${target}`;
    physicalByEndpoints.set(
      key,
      physicalByEndpoints.has(key)
        ? mergePhysicalEdge(physicalByEndpoints.get(key), normalized)
        : normalized,
    );
  }

  const nodes = [...baseNodes, ...externalNodes.values()].sort((left, right) => (
    (NODE_KIND_ORDER[left.kind] ?? 9) - (NODE_KIND_ORDER[right.kind] ?? 9)
    || compareText(left.label, right.label)
    || compareText(left.id, right.id)
  ));
  const physicalEdges = [...physicalByEndpoints.values()]
    .map(({ observationCount, ...edge }) => edge)
    .sort((left, right) => compareText(left.id, right.id));
  return { nodes, logicalEdges, physicalEdges };
}

function clamp(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum);
}

function distribute(nodes, x, minY, maxY) {
  if (!nodes.length) return [];
  const step = nodes.length === 1 ? 0 : (maxY - minY) / (nodes.length - 1);
  return nodes.map((node, index) => ({ ...node, x, y: minY + (step * index) }));
}

function distributeAssetGrid(nodes, baseX, minY, maxY, width) {
  const columnCount = width >= 900 && nodes.length > 4 ? 2 : 1;
  if (columnCount === 1) return distribute(nodes, baseX, minY, maxY);
  const columns = Array.from({ length: columnCount }, () => []);
  nodes.forEach((node, index) => columns[index % columnCount].push(node));
  return columns.flatMap((column, index) => (
    distribute(column, baseX + (index * Math.min(120, width * 0.12)), minY, maxY)
  ));
}

export function layoutTopology(graph, viewport = {}) {
  const width = Math.max(320, Number(viewport.width) || 960);
  const height = Math.max(320, Number(viewport.height) || 640);
  const padding = width < 480 ? 20 : 32;
  const minY = padding + 28;
  const maxY = height - padding - 28;
  const groups = {
    root: graph.nodes.filter((node) => node.kind === 'root'),
    category: graph.nodes.filter((node) => node.kind === 'category'),
    asset: graph.nodes.filter((node) => node.kind === 'asset'),
    external: graph.nodes.filter((node) => node.kind === 'external'),
  };
  const columns = {
    root: padding + 20,
    category: clamp(width * 0.32, padding + 72, width - padding),
    asset: clamp(width * 0.61, padding + 150, width - padding - 72),
    external: width - padding - 20,
  };
  const positioned = [
    ...distribute(groups.root, columns.root, minY, maxY),
    ...distribute(groups.category, columns.category, minY, maxY),
    ...distributeAssetGrid(groups.asset, columns.asset, minY, maxY, width),
    ...distribute(groups.external, columns.external, minY, maxY),
  ].map((node) => ({
    ...node,
    x: clamp(Math.round(node.x), padding, width - padding),
    y: clamp(Math.round(node.y), padding, height - padding),
  }));

  return {
    nodes: positioned,
    logicalEdges: graph.logicalEdges.map((edge) => ({ ...edge })),
    physicalEdges: graph.physicalEdges.map((edge) => ({ ...edge })),
    legend: [
      { kind: 'logical', label: '逻辑归属' },
      { kind: 'physical', label: 'LLDP/CDP 物理发现' },
      { kind: 'external', label: '未解析邻居' },
    ],
    bounds: { minX: padding, minY: padding, maxX: width - padding, maxY: height - padding },
  };
}
