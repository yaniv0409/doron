import { useEffect, useRef } from "react";
import { DataSet } from "vis-data";
import { Network } from "vis-network";
import "vis-network/styles/vis-network.css";

export default function GraphPanel({ graph, onInspect }) {
  const containerRef = useRef(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return undefined;
    }
    let disposed = false;

    const network = new Network(
      container,
      {
        nodes: new DataSet(
          graph.nodes.map((node) => ({
            id: node.id,
            label: node.label,
            title: node.kind,
            shape: "dot",
            size: 18,
          })),
        ),
        edges: new DataSet(
          graph.edges.map((edge) => ({
            id: edge.id,
            from: edge.source,
            to: edge.target,
            label: edge.label,
            arrows: "to",
          })),
        ),
      },
      {
        autoResize: true,
        layout: { improvedLayout: true },
        physics: { stabilization: true },
        interaction: { hover: true },
        nodes: {
          color: {
            background: "#f9c74f",
            border: "#7ea6c7",
            highlight: { background: "#43aa8b", border: "#d7e5f3" },
          },
          font: { color: "#08111b", face: "Georgia" },
        },
        edges: {
          color: "#6f91b3",
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
