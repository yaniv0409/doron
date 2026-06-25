import { useEffect, useRef } from "react";
import { DataSet } from "vis-data";
import { Network } from "vis-network";
import "vis-network/styles/vis-network.css";

const NODE_SIZE = 18;
const HUE_SATURATION = 0.68;
const VALUE_MIN = 0.52;
const VALUE_MAX = 0.96;
const EDGE_SATURATION = 0.56;
const EDGE_VALUE = 0.82;

export default function GraphPanel({ graph, onInspect }) {
  const containerRef = useRef(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return undefined;
    }
    let disposed = false;
    const nodeDegrees = buildNodeDegreeMap(graph);

    const network = new Network(
      container,
      {
        nodes: new DataSet(
          graph.nodes.map((node) => buildGraphNode(node, nodeDegrees)),
        ),
        edges: new DataSet(
          graph.edges.map((edge) => buildGraphEdge(edge)),
        ),
      },
      {
        autoResize: true,
        layout: { improvedLayout: true },
        physics: { stabilization: true },
        interaction: { hover: true },
        nodes: {
          font: { color: "#08111b", face: "Georgia" },
        },
        edges: {
          font: { align: "middle", color: "#d4e0ec" },
        },
      },
    );

    function refreshLayout() {
      if (disposed || !network || !network.body?.container) {
        return;
      }
      if (!container.clientWidth || !container.clientHeight) {
        return;
      }
      try {
        network.redraw();
        network.fit({
          animation: false,
        });
      } catch {
        // Ignore late resize events during teardown.
      }
    }

    const resizeObserver = new ResizeObserver(() => {
      refreshLayout();
    });
    resizeObserver.observe(container);

    requestAnimationFrame(() => {
      refreshLayout();
    });

    network.on("click", (event) => {
      const [nodeId] = event.nodes;
      const [edgeId] = event.edges;
      if (nodeId) {
        const node = graph.nodes.find((item) => item.id === nodeId);
        if (node) {
          onInspect({ type: "node", ...node });
        }
      } else if (edgeId) {
        const edge = graph.edges.find((item) => item.id === edgeId);
        if (edge) {
          onInspect({ type: "edge", ...edge });
        }
      }
    });

    return () => {
      disposed = true;
      resizeObserver.disconnect();
      network.destroy();
    };
  }, [graph, onInspect]);

  return <div className="graph-canvas" ref={containerRef} />;
}

function buildGraphNode(node, nodeDegrees) {
  const degree = nodeDegrees.get(node.id) || 0;
  const maxDegree = Math.max(...nodeDegrees.values(), 0);
  const hue = hashNodeKind(node.kind);
  const value = resolveNodeBrightness(degree, maxDegree);
  const background = hsvToHex(hue, HUE_SATURATION, value);
  const border = hsvToHex(hue, HUE_SATURATION * 0.8, Math.max(0.24, value - 0.28));
  const highlight = hsvToHex(hue, HUE_SATURATION * 0.72, Math.min(1, value + 0.08));

  return {
    id: node.id,
    label: node.label,
    title: `${node.kind} (${degree} visible ${degree === 1 ? "edge" : "edges"})`,
    shape: "dot",
    size: NODE_SIZE,
    color: {
      background,
      border,
      highlight: {
        background: highlight,
        border: "#d7e5f3",
      },
      hover: {
        background: highlight,
        border: "#d7e5f3",
      },
    },
  };
}

function buildNodeDegreeMap(graph) {
  const degrees = new Map(graph.nodes.map((node) => [node.id, 0]));
  for (const edge of graph.edges) {
    degrees.set(edge.source, (degrees.get(edge.source) || 0) + 1);
    degrees.set(edge.target, (degrees.get(edge.target) || 0) + 1);
  }
  return degrees;
}

function buildGraphEdge(edge) {
  const hue = hashGraphType(edge.label);
  const color = hsvToHex(hue, EDGE_SATURATION, EDGE_VALUE);
  const highlight = hsvToHex(hue, EDGE_SATURATION * 0.68, Math.min(1, EDGE_VALUE + 0.12));

  return {
    id: edge.id,
    from: edge.source,
    to: edge.target,
    arrows: "to",
    color: {
      color,
      highlight,
      hover: highlight,
      inherit: false,
    },
  };
}

function hashNodeKind(kind) {
  return hashGraphType(kind);
}

function hashGraphType(value) {
  const text = String(value || "item");
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) % 360;
  }
  return hash;
}

function resolveNodeBrightness(degree, maxDegree) {
  if (maxDegree <= 0) {
    return VALUE_MIN;
  }
  const ratio = degree / maxDegree;
  return VALUE_MIN + ratio * (VALUE_MAX - VALUE_MIN);
}

function hsvToHex(hue, saturation, value) {
  const { red, green, blue } = hsvToRgb(hue, saturation, value);
  return `#${toHex(red)}${toHex(green)}${toHex(blue)}`;
}

function hsvToRgb(hue, saturation, value) {
  const chroma = value * saturation;
  const segment = (hue / 60) % 6;
  const secondary = chroma * (1 - Math.abs((segment % 2) - 1));
  const match = value - chroma;

  let red = 0;
  let green = 0;
  let blue = 0;

  if (segment >= 0 && segment < 1) {
    red = chroma;
    green = secondary;
  } else if (segment < 2) {
    red = secondary;
    green = chroma;
  } else if (segment < 3) {
    green = chroma;
    blue = secondary;
  } else if (segment < 4) {
    green = secondary;
    blue = chroma;
  } else if (segment < 5) {
    red = secondary;
    blue = chroma;
  } else {
    red = chroma;
    blue = secondary;
  }

  return {
    red: Math.round((red + match) * 255),
    green: Math.round((green + match) * 255),
    blue: Math.round((blue + match) * 255),
  };
}

function toHex(value) {
  return value.toString(16).padStart(2, "0");
}
