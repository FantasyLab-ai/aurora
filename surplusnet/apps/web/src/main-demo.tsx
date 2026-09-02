import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { setApiBackend } from './api';
import { engineApi } from './demo/engine-api';
import './styles.css';

// Self-contained demo: the real engine runs in this page — no server.
setApiBackend(engineApi);

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
