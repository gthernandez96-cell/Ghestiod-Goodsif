import os
import sys
import httpx
import sqlite3
import shutil
from decimal import Decimal

TOKEN = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
USERNAME = "gthernandez96"
BASE_URL = f"https://www.pythonanywhere.com/api/v0/user/{USERNAME}"
HEADERS = {"Authorization": f"Token {TOKEN}"}
DOMAIN = f"{USERNAME}.pythonanywhere.com"

FILES_TO_DEPLOY = [
    # Backend Models & APIs
    "backend/app/models/sale.py",
    "backend/app/api/v1/sales.py",
    "backend/app/api/v1/reports.py",
    "backend/app/services/fel_service.py",
    "backend/app/api/pages.py",
    "backend/app/api/v1/ar.py",
    "backend/app/api/v1/auth.py",
    "backend/app/api/v1/cashier.py",
    "backend/app/api/v1/dashboard.py",
    "backend/app/api/v1/inventory.py",
    "backend/app/api/v1/partners.py",
    "backend/app/api/v1/products.py",
    "backend/app/api/v1/users.py",
    
    # Templates
    "backend/app/templates/pos/index.html",
    "backend/app/templates/reports/index.html",
    "backend/app/templates/customers/index.html",
    "backend/app/templates/customers/form.html",
    "backend/app/templates/pos/sessions.html",
    "backend/app/templates/inventory/index.html",
    "backend/app/templates/inventory/form.html",
]

def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    print("🚀 === INICIANDO SINCRONIZACIÓN Y RECONCILIACIÓN EN PRODUCCIÓN ===")
    
    with httpx.Client(verify=False, timeout=120.0) as client:
        # Paso 1: Descargar base de datos actual de producción
        print("\n📥 Paso 1: Descargando pos.db de producción...")
        db_remote_url = f"{BASE_URL}/files/path/home/{USERNAME}/pos-empresarial/pos.db"
        resp = client.get(db_remote_url, headers=HEADERS)
        if resp.status_code != 200:
            print(f"❌ Error al descargar base de datos: HTTP {resp.status_code}")
            sys.exit(1)
            
        backup_path = os.path.join(root_dir, "pos_prod_backup_pre_fix.db")
        reconciled_path = os.path.join(root_dir, "pos_prod_final_reconciled.db")
        with open(backup_path, "wb") as f:
            f.write(resp.content)
        print(f"✅ Copia de respaldo guardada ({len(resp.content)} bytes) en {backup_path}")
        
        shutil.copyfile(backup_path, reconciled_path)
        
        # Paso 2: Reconciliar datos tributarios y precios de catálogo
        print("\n⚙️ Paso 2: Reconciliando órdenes de venta y cálculos tributarios...")
        conn = sqlite3.connect(reconciled_path)
        c = conn.cursor()
        
        c.execute('SELECT id, number, subtotal, tax_amount, total, amount_paid, amount_due, state FROM sale_orders')
        sales = c.fetchall()
        
        reconciled_count = 0
        for s_id, s_num, old_sub, old_tax, old_tot, amt_paid_val, amt_due_val, state_val in sales:
            c.execute('SELECT id, qty, unit_price, discount_amount, subtotal, tax_amount, total FROM sale_order_lines WHERE sale_id=?', (s_id,))
            lines = c.fetchall()
            if not lines:
                continue
            
            # Verificar si las líneas tenían el impuesto sumado encima del precio unitario
            is_affected = False
            for l_id, l_qty, l_price, l_disc, l_sub, l_tax, l_tot in lines:
                qty_d = Decimal(str(l_qty or 0))
                price_d = Decimal(str(l_price or 0))
                disc_d = Decimal(str(l_disc or 0))
                gross = qty_d * price_d - disc_d
                if gross > 0 and abs(Decimal(str(l_sub or 0)) - gross) < Decimal('0.05') and Decimal(str(l_tax or 0)) > 0:
                    is_affected = True
                    break
                    
            if not is_affected:
                continue

            reconciled_count += 1
            tot_sub = Decimal('0')
            tot_tax = Decimal('0')
            tot_tot = Decimal('0')
            for l_id, l_qty, l_price, l_disc, l_sub, l_tax, l_tot in lines:
                qty_d = Decimal(str(l_qty or 0))
                price_d = Decimal(str(l_price or 0))
                disc_d = Decimal(str(l_disc or 0))
                gross = qty_d * price_d
                line_net = gross - disc_d
                new_sub = (line_net / Decimal('1.12')).quantize(Decimal('0.0001'))
                new_tax = (line_net - new_sub).quantize(Decimal('0.0001'))
                new_tot = line_net.quantize(Decimal('0.0001'))
                c.execute('UPDATE sale_order_lines SET subtotal=?, tax_amount=?, total=? WHERE id=?',
                          (float(new_sub), float(new_tax), float(new_tot), l_id))
                tot_sub += new_sub
                tot_tax += new_tax
                tot_tot += new_tot

            final_tot = tot_tot.quantize(Decimal('0.01'))
            final_sub = tot_sub.quantize(Decimal('0.01'))
            final_tax = tot_tax.quantize(Decimal('0.01'))
            amt_paid_d = Decimal(str(amt_paid_val or 0))

            if amt_paid_d >= final_tot - Decimal('0.05'):
                new_due = Decimal('0.0')
                new_state = 'PAID'
                new_paid = final_tot
                # Sincronizar montos de pagos si eran por el subtotal antiguo
                c.execute("UPDATE sale_payments SET amount=? WHERE sale_id=? AND abs(amount - ?) < 0.05",
                          (float(final_tot), s_id, float(amt_paid_d)))
            else:
                new_due = max(Decimal('0.0'), (final_tot - amt_paid_d).quantize(Decimal('0.01')))
                new_state = 'PAID' if new_due == Decimal('0.0') else 'CONFIRMED'
                new_paid = amt_paid_d

            c.execute('''
                UPDATE sale_orders
                SET subtotal=?, tax_amount=?, total=?, amount_paid=?, amount_due=?, state=?
                WHERE id=?
            ''', (float(final_sub), float(final_tax), float(final_tot), float(new_paid), float(new_due), new_state, s_id))

        # Reconciliar Ticket POS-20260910-94662300 para asegurar contado
        c.execute("""
            UPDATE sale_payments 
            SET payment_method='cash', amount=46.75 
            WHERE sale_id IN (SELECT id FROM sale_orders WHERE number LIKE '%94662300%')
        """)
        c.execute("""
            UPDATE sale_orders 
            SET sale_type='POS', state='PAID', amount_paid=46.75, amount_due=0.0, total=46.75 
            WHERE number LIKE '%94662300%'
        """)

        # Recalcular saldos de clientes registrados en partners
        c.execute("SELECT id FROM partners")
        all_partners = c.fetchall()
        for (p_id,) in all_partners:
            c.execute("""
                SELECT COALESCE(SUM(p.amount), 0)
                FROM sale_payments p
                JOIN sale_orders s ON p.sale_id = s.id
                WHERE s.customer_id = ? AND p.payment_method = 'credit' AND s.state != 'CANCELLED'
            """, (p_id,))
            cred_sales = c.fetchone()[0]
            c.execute("""
                SELECT COALESCE(SUM(amount), 0)
                FROM customer_credit_payments
                WHERE customer_id = ?
            """, (p_id,))
            abonos = c.fetchone()[0]
            real_bal = max(0.0, float(cred_sales) - float(abonos))
            c.execute("UPDATE partners SET balance=? WHERE id=?", (real_bal, p_id))

        conn.commit()
        conn.close()
        print(f"✅ Se reconciliaron exitosamente {reconciled_count} órdenes de venta.")
        
        # Paso 3: Desplegar archivos de código modificados
        print(f"\n📤 Paso 3: Desplegando {len(FILES_TO_DEPLOY)} archivos de código...")
        for i, rel_path in enumerate(FILES_TO_DEPLOY, 1):
            local_path = os.path.join(root_dir, rel_path)
            with open(local_path, "rb") as f:
                content = f.read()
            remote_url = f"{BASE_URL}/files/path/home/{USERNAME}/pos-empresarial/{rel_path}"
            fname = os.path.basename(rel_path)
            resp = client.post(
                remote_url,
                files={"content": (fname, content, "application/octet-stream")},
                headers=HEADERS
            )
            if resp.status_code in (200, 201):
                print(f"  [{i}/{len(FILES_TO_DEPLOY)}] ✅ {rel_path} - OK")
            else:
                print(f"  [{i}/{len(FILES_TO_DEPLOY)}] ❌ Error en {rel_path}: HTTP {resp.status_code}")
                sys.exit(1)
                
        # Paso 4: Subir la base de datos reconciliada
        print("\n📤 Paso 4: Subiendo pos.db reconciliada a producción...")
        with open(reconciled_path, "rb") as f:
            db_content = f.read()
        resp_db = client.post(
            db_remote_url,
            files={"content": ("pos.db", db_content, "application/octet-stream")},
            headers=HEADERS
        )
        if resp_db.status_code in (200, 201):
            print(f"✅ Base de datos subida exitosamente ({len(db_content)} bytes).")
        else:
            print(f"❌ Error al subir base de datos: HTTP {resp_db.status_code}: {resp_db.text}")
            sys.exit(1)
            
        # Paso 5: Recargar aplicación web
        print(f"\n🔄 Paso 5: Recargando webapp en {DOMAIN}...")
        reload_url = f"{BASE_URL}/webapps/{DOMAIN}/reload/"
        r_reload = client.post(reload_url, headers=HEADERS)
        if r_reload.status_code == 200:
            print(f"✅ Webapp recargada exitosamente (HTTP 200).")
        else:
            print(f"⚠️ Advertencia en recarga: HTTP {r_reload.status_code}")
            
        # Paso 6: Verificación de salud
        print(f"\n🌐 Paso 6: Verificando disponibilidad del sistema...")
        r_live = client.get(f"https://{DOMAIN}/login", timeout=20.0, follow_redirects=True)
        print(f"  Status: HTTP {r_live.status_code}")
        if r_live.status_code == 200:
            print("  🎉 ¡Sistema en producción 100% operativo y actualizado!")
        else:
            print(f"  ⚠️ Código de respuesta: {r_live.status_code}")

if __name__ == "__main__":
    main()
