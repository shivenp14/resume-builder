import React from 'react';

export function SuccessNotice({ children }) {
  return <div className="success-banner" role="status">
    <svg className="success-check" aria-hidden="true" width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="8" />
      <path pathLength="1" d="m6 10 2.6 2.6L14 7.2" />
    </svg>
    <span>{children}</span>
  </div>;
}
