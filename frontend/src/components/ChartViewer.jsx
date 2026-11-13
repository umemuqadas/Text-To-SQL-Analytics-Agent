import { useEffect, useRef } from 'react'

// plotly.js is ~3 MB, so it is loaded only when a chart is actually rendered.
let plotlyPromise = null
const loadPlotly = () => (plotlyPromise ??= import('plotly.js-dist-min').then((m) => m.default))

const LAYOUT = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: { color: '#8d9296', size: 12, family: 'system-ui, sans-serif' },
  margin: { l: 56, r: 16, t: 28, b: 40 },
  xaxis: { gridcolor: '#2b2e31', zerolinecolor: '#2b2e31', linecolor: '#2b2e31' },
  yaxis: { gridcolor: '#2b2e31', zerolinecolor: '#2b2e31', linecolor: '#2b2e31' },
}

/** Renders a plotly figure JSON produced by the Visualization Agent. */
export default function ChartViewer({ figure, chartType, height = 300 }) {
  const ref = useRef(null)

  useEffect(() => {
    if (!ref.current || !figure || chartType === 'kpi') return
    const node = ref.current
    let disposed = false

    loadPlotly().then((Plotly) => {
      if (disposed || !node) return
      const layout = { ...LAYOUT, ...figure.layout, xaxis: { ...LAYOUT.xaxis, ...figure.layout?.xaxis }, yaxis: { ...LAYOUT.yaxis, ...figure.layout?.yaxis } }
      Plotly.newPlot(node, figure.data || [], layout, { responsive: true, displaylogo: false })
    })

    return () => {
      disposed = true
      loadPlotly().then((Plotly) => node && Plotly.purge(node))
    }
  }, [figure, chartType])

  if (chartType === 'kpi' || !figure) return null

  return (
    <div className="panel p-3">
      <div ref={ref} style={{ height }} />
    </div>
  )
}
