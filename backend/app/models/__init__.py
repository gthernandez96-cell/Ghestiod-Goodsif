"""Importar todos los modelos para que Alembic los detecte."""
from app.models.base import BaseModel
from app.models.company import Company, Branch, Warehouse, TaxRegime
from app.models.user import User, Role, Permission, user_roles, user_branches, role_permissions
from app.models.product import (
    Product, ProductCategory, ProductVariant, ProductPrice, PriceList,
    UnitOfMeasure, ProductSupplier, ProductType, TrackingMethod
)
from app.models.inventory import StockMove, StockLot, StockQuant, PhysicalInventory, PhysicalInventoryLine, StockMoveType
from app.models.partner import Partner, PartnerType
from app.models.cashier import CashierSession, POSTerminal, CashMovement
from app.models.sale import SaleOrder, SaleOrderLine, SalePayment, POSSession
from app.models.purchase import PurchaseOrder, PurchaseOrderLine, GoodsReceipt, GoodsReceiptLine
from app.models.transfer import Transfer, TransferLine
from app.models.fel import FELDocument, FELSequence

__all__ = [
    "BaseModel",
    "Company", "Branch", "Warehouse", "TaxRegime",
    "User", "Role", "Permission",
    "Product", "ProductCategory", "ProductVariant", "ProductPrice", "PriceList",
    "UnitOfMeasure", "ProductSupplier", "ProductType", "TrackingMethod",
    "StockMove", "StockLot", "StockQuant", "PhysicalInventory", "PhysicalInventoryLine", "StockMoveType",
    "Partner", "PartnerType",
    "CashierSession", "POSTerminal", "CashMovement",
    "SaleOrder", "SaleOrderLine", "SalePayment", "POSSession",
    "PurchaseOrder", "PurchaseOrderLine", "GoodsReceipt", "GoodsReceiptLine",
    "Transfer", "TransferLine",
    "FELDocument", "FELSequence",
]
