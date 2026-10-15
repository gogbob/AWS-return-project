import React, { createContext, useContext, useState } from 'react';

interface CustomerContextValue {
  customerId: string;
  setCustomerId: (id: string) => void;
}

const CustomerContext = createContext<CustomerContextValue>({
  customerId: '',
  setCustomerId: () => undefined,
});

export function CustomerProvider({ children }: { children: React.ReactNode }) {
  const [customerId, setCustomerId] = useState('');
  return (
    <CustomerContext.Provider value={{ customerId, setCustomerId }}>
      {children}
    </CustomerContext.Provider>
  );
}

export function useCustomer() {
  return useContext(CustomerContext);
}
