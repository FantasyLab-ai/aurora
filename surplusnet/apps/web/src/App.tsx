import { useState } from 'react';
import { RecipientView } from './views/RecipientView';
import { CourierView } from './views/CourierView';
import { SupplierView } from './views/SupplierView';
import { OpsView } from './views/OpsView';

type Role = 'recipient' | 'courier' | 'supplier' | 'ops';

const ROLES: Array<{ id: Role; label: string }> = [
  { id: 'recipient', label: '🛍 Recipient' },
  { id: 'courier', label: '🚲 Courier' },
  { id: 'supplier', label: '🏪 Supplier' },
  { id: 'ops', label: '🗺 Ops' },
];

export function App() {
  const [role, setRole] = useState<Role>('recipient');
  return (
    <>
      <header className="topbar">
        <div className="brand">
          Surplus<span>Net</span>
          <span className="zone">Greenpoint</span>
        </div>
        <div className="role-switch">
          {ROLES.map((r) => (
            <button key={r.id} className={r.id === role ? 'active' : ''} onClick={() => setRole(r.id)}>
              {r.label}
            </button>
          ))}
        </div>
      </header>
      {role === 'recipient' && <RecipientView />}
      {role === 'courier' && <CourierView />}
      {role === 'supplier' && <SupplierView />}
      {role === 'ops' && <OpsView />}
    </>
  );
}
