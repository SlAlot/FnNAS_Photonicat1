#!/usr/bin/env python3
"""Port the official board's legacy XPCS setup onto pinned Linux 6.18.18."""
import difflib
from pathlib import Path
import sys
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]

def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'Unexpected upstream source context: {old[:90]!r}')
    return source.replace(old, new, 1)

def prepare(original):
    source = original
    source = replace_once(source, '#include <linux/phy.h>', '#include <linux/phy.h>\n#include <linux/phy/phy.h>\n#include <linux/mdio.h>')
    # Avoid the name collision with the generic PHY subsystem.
    source = source.replace('phy_power_on(', 'rk_gmac_phy_power_on(')
    source = replace_once(source, '"aclk_mac", "pclk_mac", "mac_clk_tx", "clk_mac_speed",',
                          '"aclk_mac", "pclk_mac", "pclk_xpcs", "mac_clk_tx", "clk_mac_speed",')
    source = replace_once(source, '\tRK_PCLK_MAC,\n', '\tRK_PCLK_MAC,\n\tRK_PCLK_XPCS,\n')
    source = replace_once(source, '\tstruct regmap *php_grf;\n', '\tstruct regmap *php_grf;\n\tstruct regmap *xpcs;\n')
    patch = (ROOT / 'vendor/rockchip-sgmii.patch').read_text()
    lock = json.loads((ROOT / 'sources.lock.json').read_text())
    assert hashlib.sha256(patch.encode()).hexdigest() == lock['official_board']['patch_sha256'], 'Official patch changed'
    start = patch.index('+/* XPCS */')
    end = patch.index(' #define PX30_GRF_GMAC_CON1', start)
    legacy = '\n'.join(line[1:] for line in patch[start:end].splitlines() if line.startswith('+')) + '\n'
    legacy = legacy.replace('&bsp_priv->pdev->dev', 'bsp_priv->dev')
    source = replace_once(source, 'static int rk_set_reg_speed(', legacy + '\nstatic int rk_set_reg_speed(')
    # Only this board's actual SGMII path is enabled; other SoC paths retain upstream behavior.
    source = replace_once(source, 'static const struct rk_gmac_ops rk3568_ops = {', '''static int pcat_rk3568_set_speed(struct rk_priv_data *bsp_priv,
                                phy_interface_t interface, int speed)
{
    if (interface == PHY_INTERFACE_MODE_SGMII)
        return 0; /* XPCS adapts the negotiated rate; SerDes stays at 125 MHz. */
    return rk_set_clk_mac_speed(bsp_priv, interface, speed);
}

static const struct rk_gmac_ops rk3568_ops = {''')
    source = replace_once(source, '''static const struct rk_gmac_ops rk3568_ops = {
\t.set_to_rgmii = rk3568_set_to_rgmii,
\t.set_to_rmii = rk3568_set_to_rmii,
\t.set_speed = rk_set_clk_mac_speed,''', '''static const struct rk_gmac_ops rk3568_ops = {
\t.set_to_rgmii = rk3568_set_to_rgmii,
\t.set_to_rmii = rk3568_set_to_rmii,
\t.set_speed = pcat_rk3568_set_speed,''')
    source = replace_once(source, 'static struct rk_priv_data *rk_gmac_setup(', '''static void pcat_serdes_exit(void *data)
{
    phy_exit(data);
}

static struct rk_priv_data *rk_gmac_setup(''')
    source = replace_once(source, '\tif (plat->phy_node) {', '''    if (bsp_priv->phy_iface == PHY_INTERFACE_MODE_SGMII) {
        struct phy *serdes;

        if (ops != &rk3568_ops)
            return ERR_PTR(-EOPNOTSUPP);
        bsp_priv->xpcs = syscon_regmap_lookup_by_phandle(dev->of_node,
                                                       "rockchip,xpcs");
        if (IS_ERR(bsp_priv->xpcs))
            return ERR_CAST(bsp_priv->xpcs);
        serdes = devm_of_phy_get(dev, dev->of_node, NULL);
        if (IS_ERR(serdes))
            return ERR_CAST(serdes);
        ret = phy_init(serdes);
        if (ret)
            return ERR_PTR(ret);
        ret = devm_add_action_or_reset(dev, pcat_serdes_exit, serdes);
        if (ret)
            return ERR_PTR(ret);
    }

\tif (plat->phy_node) {''')
    source = replace_once(source, '''static int rk_gmac_check_ops(struct rk_priv_data *bsp_priv)
{
\tswitch (bsp_priv->phy_iface) {''', '''static int rk_gmac_check_ops(struct rk_priv_data *bsp_priv)
{
\tswitch (bsp_priv->phy_iface) {
    case PHY_INTERFACE_MODE_SGMII:
        return bsp_priv->ops == &rk3568_ops ? 0 : -EOPNOTSUPP;''')
    source = replace_once(source, '''\t/*rmii or rgmii*/
\tswitch (bsp_priv->phy_iface) {''', '''\t/* Parallel interfaces and the board's legacy SGMII path. */
\tswitch (bsp_priv->phy_iface) {
    case PHY_INTERFACE_MODE_SGMII:
        regmap_write(bsp_priv->grf,
                     bsp_priv->id == 1 ? RK3568_GRF_GMAC1_CON1 : RK3568_GRF_GMAC0_CON1,
                     GRF_BIT(7));
        ret = xpcs_setup(bsp_priv, PHY_INTERFACE_MODE_SGMII);
        if (ret) {
            gmac_clk_enable(bsp_priv, false);
            return ret;
        }
        dev_info(dev, "Photonicat SGMII initialized\\n");
        break;''')
    source = replace_once(source, '''\tif (rk->ops->set_to_rmii)
\t\t__set_bit(PHY_INTERFACE_MODE_RMII, interfaces);
}''', '''\tif (rk->ops->set_to_rmii)
\t\t__set_bit(PHY_INTERFACE_MODE_RMII, interfaces);

    if (rk->ops == &rk3568_ops && rk->phy_iface == PHY_INTERFACE_MODE_SGMII)
        __set_bit(PHY_INTERFACE_MODE_SGMII, interfaces);
}''')
    source += '\nMODULE_VERSION("0.1.0-photonicat-test");\n'
    return source

def main():
    path = Path(sys.argv[1])
    original = path.read_text()
    source = prepare(original)
    path.write_text(source)
    path.with_suffix('.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True), source.splitlines(True),
                         fromfile='a/dwmac-rk.c', tofile='b/dwmac-rk.c')))
    (path.parent / 'Makefile').write_text('obj-m += dwmac-rk.o\n')
    print('Prepared source-built Photonicat driver; no binary instruction patching.')

if __name__ == '__main__':
    main()
