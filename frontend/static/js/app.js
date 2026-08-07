/**
 * JavaScript global del sistema POS Empresarial.
 */

// Utilidades globales
window.POSUtils = {
  formatCurrency(amount) {
    return 'Q ' + Number(amount || 0).toFixed(2);
  },

  formatDate(dateString) {
    if (!dateString) return '';
    const d = new Date(dateString);
    return d.toLocaleDateString('es-GT', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  }
};
