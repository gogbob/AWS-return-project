import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { CustomerProvider } from './context/CustomerContext';
import { BagProvider } from './context/BagContext';
import ProductPage from './pages/ProductPage';
import AdminPage from './pages/AdminPage';

export default function App() {
  return (
    <CustomerProvider>
      <BagProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<ProductPage />} />
            <Route path="/admin" element={<AdminPage />} />
          </Routes>
        </BrowserRouter>
      </BagProvider>
    </CustomerProvider>
  );
}
