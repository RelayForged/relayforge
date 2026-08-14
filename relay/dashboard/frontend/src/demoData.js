// DEMO / PLACEHOLDER
// Replace with real delivery events when the delivery component is available.

export const demoDeliveryEvents = [
  {
    id: 1,
    timestamp: '12:34:01',
    type: 'REQUEST_RECEIVED',
  },
  {
    id: 2,
    timestamp: '12:34:02',
    type: 'ROUTE_SELECTED',
  },
  {
    id: 3,
    timestamp: '12:34:03',
    type: 'DELIVERY_ATTEMPT',
  },
  {
    id: 4,
    timestamp: '12:34:04',
    type: 'DELIVERY_SUCCESS',
  },
]

// DEMO / PLACEHOLDER
// Replace with the real policy-engine output when available.

export const demoPolicyDecision = {
  decision: 'PRIMARY',
  selectedTarget: 'endpoint-A',
  reason: 'Endpoint available',
}

// DEMO / PLACEHOLDER
// Replace with real policy explanation data when available.

export const demoPolicyExplanation = {
  title: 'Why was PRIMARY selected?',
  reasons: [
    'Endpoint-A is AVAILABLE',
    'ERI: 100',
    'No active quarantine',
    'Primary route is currently preferred',
  ],
}