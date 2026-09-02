/**
 * JavaScript global del sistema POS Empresarial.
 */

// Utilidades globales
window.POSUtils = {
  formatCurrency(amount) {
    return 'Q ' + Number(amount || 0).toFixed(2);
  },

  formatDate(dateString, includeTime = false) {
    if (!dateString) return '';
    if (typeof dateString === 'string' && /^\d{2}\/\d{2}\/\d{4}/.test(dateString)) {
      return dateString;
    }
    const d = new Date(dateString);
    if (isNaN(d.getTime())) return String(dateString);
    const day = String(d.getDate()).padStart(2, '0');
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const year = d.getFullYear();
    if (!includeTime) {
      return `${day}/${month}/${year}`;
    }
    const hours = String(d.getHours()).padStart(2, '0');
    const minutes = String(d.getMinutes()).padStart(2, '0');
    return `${day}/${month}/${year} ${hours}:${minutes}`;
  }
};
