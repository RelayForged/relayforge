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
    </main>
  )
}

export default App