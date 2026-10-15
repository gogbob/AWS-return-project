import React, { createContext, useContext, useState } from 'react';

export interface BagItem {
  id: string; // unique line id (item name + size)
  item_name: string;
  size: string;
  price: number;
}

interface BagContextValue {
  items: BagItem[];
  addItem: (item: Omit<BagItem, 'id'>) => void;
  removeItem: (id: string) => void;
  clear: () => void;
  count: number;
}

const BagContext = createContext<BagContextValue>({
  items: [],
  addItem: () => undefined,
  removeItem: () => undefined,
  clear: () => undefined,
  count: 0,
});

export function BagProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<BagItem[]>([]);

  function addItem(item: Omit<BagItem, 'id'>) {
    const id = `${item.item_name}-${item.size}`;
    setItems((prev) => {
      // Avoid duplicate identical line (same item + size); keep it simple.
      if (prev.some((it) => it.id === id)) return prev;
      return [...prev, { ...item, id }];
    });
  }

  function removeItem(id: string) {
    setItems((prev) => prev.filter((it) => it.id !== id));
  }

  function clear() {
    setItems([]);
  }

  return (
    <BagContext.Provider
      value={{ items, addItem, removeItem, clear, count: items.length }}
    >
      {children}
    </BagContext.Provider>
  );
}

export function useBag() {
  return useContext(BagContext);
}
