import { useEffect, useState } from 'react'
import './App.css'

function App() {
  const [message, setMessage] = useState('Connecting...')

  useEffect(() => {
    const socket = new WebSocket('ws://127.0.0.1:8001/ws')

    socket.onmessage = (event) => {
      setMessage(event.data)
    }

    socket.onerror = () => {
      setMessage('WebSocket connection failed')
    }

    return () => {
      socket.close()
    }
  }, [])

  return (
    <main>
      <h1>RelayForge Dashboard</h1>
      <p>{message}</p>
    </main>
  )
}

export default App