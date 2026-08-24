import { useEffect, useState } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import './App.css'
import {
  demoDeliveryEvents,
  demoPolicyDecision,
  demoPolicyExplanation,
} from './demoData'

function App() {
  const [state, setState] = useState('Connecting...')
  const [eri, setEri] = useState(null)
  const [eriHistory, setEriHistory] = useState([])

  useEffect(() => {
    const socket = new WebSocket('ws://127.0.0.1:8001/ws')

    socket.onmessage = (event) => {
      const data = JSON.parse(event.data)

      if (data.type === 'state_update') {
        setState(data.state)
        setEri(data.eri)
        setEriHistory(data.eri_history ?? [])
      }
    }

    socket.onerror = () => {
      setState('WebSocket connection failed')
    }

    return () => {
      socket.close()
    }
  }, [])

  return (
    <main>
      <h1>RelayForge Dashboard</h1>

      <section>
  <h2>Endpoint State</h2>
  <span className={`status-badge status-${state.toLowerCase()}`}>
    {state}
  </span>
</section>

      <section>
        <h2>ERI</h2>
        <p>{eri ?? '—'}</p>
      </section>

      <section>
  <h2>ERI History</h2>

  <div className="chart-container">
    <ResponsiveContainer width="100%" height={300}>
      <LineChart
        data={eriHistory.map((point) => ({
  time: point.timestamp,
  eri: point.value,
}))}
      >
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis
  dataKey="time"
  tickFormatter={(timestamp) =>
    new Date(timestamp).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    })
  }
/>
        <YAxis domain={[0, 100]} />
        <Tooltip
  labelFormatter={(timestamp) =>
    new Date(timestamp).toLocaleString()
  }
/>
        <Line
          type="monotone"
          dataKey="eri"
          stroke="#2563eb"
          strokeWidth={2}
          dot
        />
      </LineChart>
    </ResponsiveContainer>
  </div>
</section>

<section>
  <h2>Delivery Timeline</h2>

  <div className="timeline">
    {demoDeliveryEvents.map((event) => (
      <div className="timeline-event" key={event.id}>
        <span className="timeline-time">{event.timestamp}</span>
        <span className="timeline-type">{event.type}</span>
      </div>
    ))}
  </div>
</section>

<section>
  <h2>Policy Decision</h2>

  <div className="policy-decision">
    <div>
      <strong>Decision:</strong>
      <span>{demoPolicyDecision.decision}</span>
    </div>

    <div>
      <strong>Selected target:</strong>
      <span>{demoPolicyDecision.selectedTarget}</span>
    </div>

    <div>
      <strong>Reason:</strong>
      <span>{demoPolicyDecision.reason}</span>
    </div>
  </div>
</section>

<section>
  <h2>Explainability</h2>

  <div className="explainability">
    <h3>{demoPolicyExplanation.title}</h3>

    <ul>
      {demoPolicyExplanation.reasons.map((reason) => (
        <li key={reason}>{reason}</li>
      ))}
    </ul>
  </div>
</section>
    </main>


  )
}

export default App