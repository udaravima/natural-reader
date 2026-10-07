import { useEffect, useRef } from 'react';

// A ref that always holds the latest `value`, updated after each render. Lets
// effects and timers call a parent's callback without listing it as a
// dependency (an inline arrow from the parent would re-run them every render).
export function useLatest(value) {
  const ref = useRef(value);
  useEffect(() => { ref.current = value; });
  return ref;
}
