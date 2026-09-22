// Full-page auth gate. No router — App wraps its render in this and only the
// `active` state mounts the app. Screens are intentionally minimal; they inherit
// the page's default styling and can be skinned later if needed.
function Screen({ title, children }) {
  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', flexDirection: 'column', gap: '1rem', padding: '2rem',
      textAlign: 'center', fontFamily: 'system-ui, sans-serif',
    }}>
      <h1 style={{ fontSize: '1.25rem', margin: 0 }}>{title}</h1>
      {children}
    </div>
  );
}

const btn = {
  padding: '0.6rem 1.2rem', borderRadius: '0.5rem', border: '1px solid #3b82f6',
  background: '#3b82f6', color: '#fff', fontWeight: 700, cursor: 'pointer',
};
const btnGhost = { ...btn, background: 'transparent', color: '#3b82f6' };

function LoginScreen({ onLogin }) {
  return (
    <Screen title="Natural Reader">
      <p>Sign in to continue.</p>
      <button style={btn} onClick={onLogin}>Sign in</button>
    </Screen>
  );
}

function PendingScreen({ onLogout }) {
  return (
    <Screen title="Your account is awaiting approval">
      <p>An administrator needs to activate your account. Check back shortly.</p>
      <div style={{ display: 'flex', gap: '0.75rem' }}>
        <button style={btn} onClick={() => window.location.reload()}>Refresh</button>
        <button style={btnGhost} onClick={onLogout}>Log out</button>
      </div>
    </Screen>
  );
}

function DisabledScreen({ onLogout }) {
  return (
    <Screen title="Your access has been disabled">
      <p>Contact an administrator if you think this is a mistake.</p>
      <button style={btn} onClick={onLogout}>Log out</button>
    </Screen>
  );
}

function AuthErrorScreen({ onRetry }) {
  return (
    <Screen title="Can't reach the server">
      <p>The backend isn't responding.</p>
      <button style={btn} onClick={onRetry}>Retry</button>
    </Screen>
  );
}

function NoAccessScreen({ onLogout, onRetry }) {
  return (
    <Screen title="Your account is active but has no access yet">
      <p>Ask an administrator to grant Reader or Chat access.</p>
      <div style={{ display: 'flex', gap: '0.75rem' }}>
        <button style={btn} onClick={onRetry}>Retry</button>
        <button style={btnGhost} onClick={onLogout}>Log out</button>
      </div>
    </Screen>
  );
}

export function AuthGate({ state, user, onLogin, onLogout, onRetry, children }) {
  switch (state) {
    case 'active':
      if (!user?.capabilities?.length) {
        return <NoAccessScreen onLogout={onLogout} onRetry={onRetry} />;
      }
      return <>{children}</>;
    case 'anonymous': return <LoginScreen onLogin={onLogin} />;
    case 'pending': return <PendingScreen onLogout={onLogout} />;
    case 'disabled': return <DisabledScreen onLogout={onLogout} />;
    case 'error': return <AuthErrorScreen onRetry={onRetry} />;
    default: return <Screen title="Loading…" />;
  }
}
