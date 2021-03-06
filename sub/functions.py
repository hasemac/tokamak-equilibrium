import coils.cmat as cmat
import numpy as np
import plasma.pmat as pmat
import vessel.vmat as vmat
from global_variables import gparam
from scipy import constants as sc

import sub.plot as pl

gl = gparam()


class dm_array:
    def __init__(self, dmat):
        self.rmin, self.rmax, self.dr = dmat["rmin"], dmat["rmax"], dmat["dr"]
        self.zmin, self.zmax, self.dz = dmat["zmin"], dmat["zmax"], dmat["dz"]
        self.nr, self.nz = int((self.rmax - self.rmin) / self.dr + 1), int(
            (self.zmax - self.zmin) / self.dz + 1
        )

        self.ir = np.array(
            [[e for e in range(self.nr)] for f in range(self.nz)]
        ).reshape(-1)
        self.iz = np.array(
            [[f for e in range(self.nr)] for f in range(self.nz)]
        ).reshape(-1)
        self.r = self.rmin + self.ir * self.dr
        self.z = self.zmin + self.iz * self.dz


# 同じ構造のdmatを取得
def get_dmat_dim(dmat):
    rmin, rmax, dr = dmat["rmin"], dmat["rmax"], dmat["dr"]
    zmin, zmax, dz = dmat["zmin"], dmat["zmax"], dmat["dz"]
    a = {
        "rmin": rmin,
        "rmax": rmax,
        "dr": dr,
        "zmin": zmin,
        "zmax": zmax,
        "dz": dz,
    }
    return a


# q[nr, nz], r, z が与えられたときに近傍３点の値から線形補間
def linval(r, z, d_mat):
    # 近傍３点の近似式について
    # 4点の値をld(左下), lu(左上), rd(右下), ru(右上)とする。
    # z = ax+by+cとしたとき、
    # それぞれの点で
    # ld = c
    # rd = a+c
    # lu = b+c
    # ru = a+b+c
    #
    # 左下に近いときはruの値を使わないでa,b,cを求めていく。
    # z = (rd-ld)x +(ru-ld)y +ld
    # 右下: luの値を使わない
    # z = (rd-ld)x +(ru-rd)y +ld
    # 左上： rdの値を使わない
    # z = (ru-lu)x +(lu-ld)y +ld
    # 右上: ldの値を使わない
    # z = (ru-lu)x +(ru-rd)y +(lu+rd-ru)
    mat = d_mat["matrix"]
    rmin = d_mat["rmin"]
    zmin = d_mat["zmin"]
    dr = d_mat["dr"]
    dz = d_mat["dz"]

    nz, nr = mat.shape

    fr = (r - rmin) / dr  # [0,1)
    ir = int(np.floor(fr))
    fr -= ir

    fz = (z - zmin) / dz  # [0, 1)
    iz = int(np.floor(fz))
    fz -= iz

    ir1 = ir + 1
    iz1 = iz + 1

    # 境界からはみ出る場合は一つ戻す。
    if nr == ir + 1:
        ir1 = ir
    if nz == iz + 1:
        iz1 = iz

    # print(ir, ir1, iz, iz1)

    ld = mat[iz, ir]
    rd = mat[iz, ir1]
    lu = mat[iz1, ir]
    ru = mat[iz1, ir1]

    v = 0
    if fr <= 0.5 and fz <= 0.5:  # 左下
        v = (rd - ld) * fr + (ru - ld) * fz + ld

    elif fr <= 1.0 and fz <= 0.5:  # 右下
        v = (rd - ld) * fr + (ru - rd) * fz + ld

    elif fr <= 0.5 and fz <= 1.0:  # 左上
        v = (ru - lu) * fr + (lu - ld) * fz + ld

    else:  # 右上
        v = (ru - lu) * fr + (ru - rd) * fz + (lu + rd - ru)

    return v


# 再サンプリング
def resampling(d_mat0, d_mat1):
    # d_mat0: 作成したい行列
    # d_mat1: もとの行列
    # 作成したい行列の情報を取得
    rmin = d_mat0["rmin"]
    zmin = d_mat0["zmin"]
    rmax = d_mat0["rmax"]
    zmax = d_mat0["zmax"]
    dr = d_mat0["dr"]
    dz = d_mat0["dz"]
    nr = int((rmax - rmin) / dr)
    nz = int((zmax - zmin) / dz)

    # nz, nr = d_mat1['matrix'].shape

    mat = [
        [linval(rmin + i * dr, zmin + j * dz, d_mat1) for i in range(nr + 1)]
        for j in range(nz + 1)
    ]
    d_mat0["matrix"] = np.array(mat)
    return d_mat0


# 加算
def dm_add(dmat0, dmat1):
    mat = dmat0["matrix"] + dmat1["matrix"]
    dmat2 = get_dmat_dim(dmat0)
    dmat2["matrix"] = mat
    return dmat2


# 正規化フラックスの計算
def get_normalized_flux(cond):
    dm_flux = cond["flux"]
    dm_domain = cond["domain"]
    # dm_flux: total flux (coil + plasma)
    # dm_domain: domain
    # return: dmat, normalized flux
    # 0: 磁気軸、1: 最外殻磁気面、0: 磁気面外
    res = get_dmat_dim(dm_domain)
    faxis, fsurf = cond["f_axis"], cond["f_surf"]
    d = dm_domain["matrix"]
    f = dm_flux["matrix"]
    f = (f - faxis) / (fsurf - faxis)  # normalized flux
    f *= d  # domainの外にあるのはゼロにする。
    res["matrix"] = f
    return res


# 圧力の微分dP/dxと、圧力Pの計算
def get_dpress_press(cond):
    dm_normalizedflux = cond["flux_normalized"]
    dm_domain = cond["domain"]
    param_press = cond["param_dp"]    
    
    g = dm_array(dm_domain)
    nr, nz = g.nr, g.nz

    f = dm_normalizedflux["matrix"].reshape(-1)
    d = dm_domain["matrix"].reshape(-1)

    # 最外殻磁気面の内部のみ取り出す
    ir = g.ir[d == 1]
    iz = g.iz[d == 1]
    r = g.r[d == 1]
    f = f[d == 1]

    npr = len(param_press)
    # 圧力の微分に関する行列
    p0 = np.array([(f**i - f**npr) for i in range(npr)])
    p0 = p0.transpose()
    p0 = np.dot(p0, param_press)
    m_dp = np.zeros((nz, nr))
    for v, i, j in zip(p0, ir, iz):
        m_dp[j, i] = v

    # 多項式の各項, n, p
    # an: x^n-x^p, これを積分すると次の式
    # an: x^(n+1)/(n+1)-x^(p+1)/(p+1), 1/(n+1)-1/(p+1) if x = 1
    # 例えば最外殻磁気面(x=1)ではプラズマ圧力がゼロとするならオフセットを各項に足す必要ある。つまり、
    # an: (x^(n+1)-1)/(n+1)-(x^(p+1)-1)/(p+1)

    # 圧力に関する行列
    p1 = np.array(
        [
            ((f ** (i + 1) - 1) / (i + 1) - (f ** (npr + 1) - 1) / (npr + 1))
            for i in range(npr)
        ]
    )
    p1 = p1.transpose()
    p1 = np.dot(p1, param_press)
    
    # 積分の変数変換に伴う係数をかける。
    p1 *= (cond['f_surf']-cond['f_axis'])
    
    m_pr = np.zeros((nz, nr))
    for v, i, j in zip(p1, ir, iz):
        m_pr[j, i] = v

    # 圧力に関してはx=1でp=0になるようにしてある。
    dm_dp = get_dmat_dim(dm_domain)
    dm_pr = get_dmat_dim(dm_domain)

    dm_dp["matrix"] = m_dp
    dm_pr["matrix"] = m_pr

    return dm_dp, dm_pr

# ポロイダルカレントの微分dI^2/dxとポロイダルカレントの計算
def get_di2_i(cond):
    dm_normalizedflux = cond["flux_normalized"]
    dm_domain = cond["domain"]
    param_i2 = cond["param_di2"]
        
    g = dm_array(dm_domain)
    nr, nz = g.nr, g.nz

    f = dm_normalizedflux["matrix"].reshape(-1)
    d = dm_domain["matrix"].reshape(-1)

    # 最外殻磁気面の内部のみ取り出す
    ir = g.ir[d == 1]
    iz = g.iz[d == 1]
    f = f[d == 1]

    npr = len(param_i2)
    # I^2の微分に関する行列
    p0 = np.array([(f**i - f**npr) for i in range(npr)])
    p0 = p0.transpose()
    p0 = np.dot(p0, param_i2)
    m_di2 = np.zeros((nz, nr))
    for v, i, j in zip(p0, ir, iz):
        m_di2[j, i] = v

    # 多項式の各項, n, p
    # an: x^n-x^p, これを積分すると次の式
    # an: x^(n+1)/(n+1)-x^(p+1)/(p+1), 1/(n+1)-1/(p+1) if x = 1
    # 例えば最外殻磁気面(x=1)ではプラズマ圧力がゼロとするならオフセットを各項に足す必要ある。つまり、
    # an: (x^(n+1)-1)/(n+1)-(x^(p+1)-1)/(p+1)

    # I^2に関する行列
    p1 = np.array(
        [
            ((f ** (i + 1) - 1) / (i + 1) - (f ** (npr + 1) - 1) / (npr + 1))
            for i in range(npr)
        ]
    )
    p1 = p1.transpose()
    p1 = np.dot(p1, param_i2)
    
    # 積分の変数変換に伴う係数をかける。
    p1 *= (cond['f_surf']-cond['f_axis'])    
    
    m_i = np.zeros((nz, nr))
    for v, i, j in zip(p1, ir, iz):
        m_i[j, i] = v
    # I^2に関してはx=1でI^2=0になるようにしてある。
    # I^2なのでIにする。
    m_i = np.sqrt(np.abs(m_i))

    dm_di2 = get_dmat_dim(dm_domain)
    dm_i = get_dmat_dim(dm_domain)

    dm_di2["matrix"] = m_di2
    dm_i["matrix"] = m_i

    return dm_di2, dm_i

# flux値の極小位置の探索
def search_local_min(dm_flx, dm_vv):
    g = dm_array(dm_flx)
    nr, nz = g.nr, g.nz

    fl, vv = dm_flx["matrix"], dm_vv["matrix"]

    ir, iz = int(nr / 2), int(nz / 2)

    while vv[iz, ir] == 1:  # 真空容器外になったら探索終了
        if fl[iz, ir] > fl[iz + 1, ir]:
            iz += 1
        elif fl[iz, ir] > fl[iz - 1, ir]:
            iz -= 1
        elif fl[iz, ir] > fl[iz, ir + 1]:
            ir += 1
        elif fl[iz, ir] > fl[iz, ir - 1]:
            ir -= 1
        else:
            break

    if vv[iz, ir] == 0:
        ir, iz = 0, 0

    return (ir, iz)

# 最外殻磁気面の探索(ip正負両方に対応)
def search_domain(cond):
    res = search_dom(cond)
    if None != res:
        return res

    # 極小値の探索に失敗しているのでfluxを反転させて再探索
    dm_flx = cond["flux"]
    dm_flx["matrix"] = -dm_flx["matrix"]
    # a = dm_flx['matrix']
    # dm_flx2 = get_dmat_dim(dm_flx)
    # dm_flx2['matrix'] = -a

    res = search_dom(cond)

    if None == res:
        # 反転させても探索失敗の場合
        return res

    dm_flx["matrix"] = -dm_flx["matrix"]
    cond["f_axis"] = -cond["f_axis"]
    cond["f_surf"] = -cond["f_surf"]

    return res


# 最外殻磁気面の探索(最小値)
def search_dom(cond):
    # dm_flx: fluxのdmat
    # dm_vv: 真空容器のdmat
    dm_flx = cond["flux"]
    dm_vv = cond["vessel"]
    g = dm_array(dm_flx)
    nz, nr = g.nz, g.nr
    dz, dr = g.dz, g.dr
    zmin, rmin = g.zmin, g.rmin

    # 返り値の作成
    res = get_dmat_dim(dm_flx)

    # 領域の初期化
    dm = np.zeros((nz, nr))

    # 真空容器内の極小値を探索してシードとして追加
    k, l = search_local_min(dm_flx, dm_vv)
    if 0 == k and 0 == l:
        return None

    dm[l, k + 1] = 1.0  # 極小値の一つ右側に設定

    # 値を記録
    cond["ir_ax"] = k
    cond["iz_ax"] = l
    cond["r_ax"] = rmin + k * dr
    cond["z_ax"] = zmin + l * dz

    # 磁気軸のフラックスを保存し、
    # 最外殻磁気面とヌル点フラックスの初期値を設定
    fax = dm_flx["matrix"][l, k]
    fsurf = fax
    fnull = fax

    # 一次元配列を作成
    m0 = dm_flx["matrix"].reshape(-1)
    vv = dm_vv["matrix"].reshape(-1)
    ir = g.ir
    iz = g.iz

    # 小さい値順に並び替える
    ix = m0.argsort()
    m0, ir, iz, vv = m0[ix], ir[ix], iz[ix], vv[ix]

    # パディング
    dm2 = np.pad(dm, [(1, 1), (1, 1)])

    for f, i, j, v in zip(m0, ir, iz, vv):
        ni, nj = i + 1, j + 1  # paddingしているので、1を足す。
        a = dm2[nj - 1 : nj + 2, ni - 1 : ni + 2].reshape(-1)
        a = np.sum(a)  # 近接点にプラズマが存在していること。
        # 新しい点に対する判定
        con = a > 0  # 存在していれば1、その他はゼロ

        # 他のプラズマと接触して、かつ真空容器内
        # この場合、プラズマの存在領域として登録
        if con and 1 == v:
            dm2[nj, ni] = con
            continue

        # 他のプラズマと接触して且つ真空容器外なら探索終了
        # リミター配位
        if con and 0 == v:
            fsurf = f
            break

        # 接触しておらず、真空容器外
        # この場合はなにもしない。

        # 接触しておらず、真空容器内
        # この場合はダイバータ配位のプライベートリージョン
        # ヌル点のフラックス値を記録しておく。
        if not con and 1 == v:
            fnull = f

    # パディング分を取り除く
    dm3 = dm2[1 : 1 + nz, 1 : 1 + nr]

    # ダイバータ配位かどうかの判定
    if fnull != fax:
        # ダイバータ配位の場合、ヌル点近傍でほとんどフラックスが変化しない。
        # ある値で切り上げたほうが多分良い。
        fsurf = fax + 0.90 * (fnull - fax)
        dm3[iz[m0 >= fsurf], ir[m0 >= fsurf]] = 0

        cond["conf_div"] = 1
    else:
        cond["conf_div"] = 0

    res["matrix"] = dm3
    cond["f_axis"] = fax
    cond["f_surf"] = fsurf

    return res


# 体積平均の算出
def get_volume_average(dm_val, dm_domain):
    # dm_val: 例えばプラズマ圧力など
    g = dm_array(dm_domain)
    dr, dz = g.dr, g.dz

    m = dm_domain["matrix"].reshape(-1)
    v = dm_val["matrix"].reshape(-1)

    # domain内のみ考える。
    r = g.r[m == 1]
    v = v[m == 1]

    vol = np.sum(2 * np.pi * r * dr * dz)  # plasma volume
    v = np.sum(2 * np.pi * r * dr * dz * v)  # vol*val
    return v / vol

# 最外殻磁気面形状
def set_domain_params(cond):
    dmat = cond["domain"]
    g = dm_array(dmat)
    rmin, dr = g.rmin, g.dr
    zmin, dz = g.zmin, g.dz

    m = dmat["matrix"].reshape(-1)
    ir = g.ir[m == 1]
    iz = g.iz[m == 1]
    r = g.r[m == 1]

    # ptsの辞書型での初期化
    cond["pts"] = {}

    # rminがある位置
    v = np.min(ir)
    r_rmin = rmin + dr * np.mean(ir[ir == v])
    z_rmin = zmin + dz * np.mean(iz[ir == v])
    cond["pts"]["r_rmin"] = r_rmin
    cond["pts"]["z_rmin"] = z_rmin

    # rmaxがある位置
    v = np.max(ir)
    r_rmax = rmin + dr * np.mean(ir[ir == v])
    z_rmax = zmin + dz * np.mean(iz[ir == v])
    cond["pts"]["r_rmax"] = r_rmax
    cond["pts"]["z_rmax"] = z_rmax

    # zminがある位置
    v = np.min(iz)
    r_zmin = rmin + dr * np.mean(ir[iz == v])
    z_zmin = zmin + dz * np.mean(iz[iz == v])
    cond["pts"]["r_zmin"] = r_zmin
    cond["pts"]["z_zmin"] = z_zmin

    # zmaxがある位置
    v = np.max(iz)
    r_zmax = rmin + dr * np.mean(ir[iz == v])
    z_zmax = zmin + dz * np.mean(iz[iz == v])
    cond["pts"]["r_zmax"] = r_zmax
    cond["pts"]["z_zmax"] = z_zmax

    a0 = (r_rmax - r_rmin) / 2.0
    r0 = (r_rmax + r_rmin) / 2.0
    cond["major_radius"] = r0
    cond["minor_radius"] = a0
    cond["elongation"] = (z_zmax - z_zmin) / (r_rmax - r_rmin)
    cond["triangularity"] = (r0 - r_zmax) / a0
    cond["volume"] = np.sum(2 * np.pi * r * dr * dz)
    cond["cross_section"] = np.sum(m[m == 1]) * dr * dz

# ipの正負に応じた値の修正
def trim_values(cond):
    jt = cond["jt"]["matrix"]
    ip = np.sum(jt)

    # ip < 0のとき、
    # pressure < 0と計算されてしまうので反転する。
    # pol_current > 0と計算されてしまうので反転する。
    if ip < 0:
        cond["pressure"]["matrix"] *= -1.0
        cond["pol_current"]["matrix"] *= -1.0

    # ポロイダル電流にTFの電流を加算
    cond["pol_current"]["matrix"] += cond["cur_tf"]["tf"] * cond["cur_tf"]["turn"]

    return cond

# ベータ値の計算
def calc_beta(cond):
    u0 = sc.mu_0  # 真空の透磁率
    pi = sc.pi

    # 圧力の体積平均
    p = get_volume_average(cond["pressure"], cond["domain"])
    cond["pressure_vol_average"] = p

    ir_ax, iz_ax = cond["ir_ax"], cond["iz_ax"]
    r_ax = cond["r_ax"]

    # 2 pi R Bt = mu0 I
    polcur = cond["pol_current"]["matrix"][iz_ax, ir_ax]
    bt = u0 * polcur / (2 * pi * r_ax)

    # toroidal beta bet_tor = <p>/(bt^2/(2u0))
    betr = p / (bt**2 / 2 / u0)
    cond["beta_toroidal"] = betr

    return cond

# safty factorの計算
def calc_safty(cond):
    # calc toroidal flux
    # ft = Integrate_area[u0*I/(2*pi*r)]
    g = dm_array(cond["domain"])
    d = cond["domain"]["matrix"].reshape(-1)
    f = cond["flux_normalized"]["matrix"].reshape(-1)
    p = cond["pol_current"]["matrix"].reshape(-1)

    # domainの場所だけ取り出す。
    f = f[d == 1]
    p = p[d == 1]
    r = g.r[d == 1]
    ir = g.ir[d == 1]
    iz = g.iz[d == 1]
    nr, nz = g.nr, g.nz

    # bfの磁気面内の面積分を行う。
    # 2 pi r bf = u0 I, thus bf = 2.0e-7 * I / t
    func = 2 * 10 ** (-7) * p / r
    func *= (g.dr*g.dz) # 面積積分なのでメッシュ面積をかける。
    
    x = np.linspace(0, 1, 11)
    y = [np.sum(func[f <= e]) for e in x]

    numpol = 2  # polynominalの次元数
    coef = np.polyfit(x, y, numpol)
    cond["coef_toroidal_flux"] = coef
    # 係数は高次の項から出力される。
    # numpol = 2の時は、２次の係数が最初。
    # 値を算出したいときは下の式を使う
    # vals = np.polyval(coef, x)

    # safty factor q = d ft/df=(1/(fb-fm))*dft(x)/dx
    dc = [numpol - e for e in range(numpol + 1)]
    dc = np.array(dc)
    # [2, 1, 0]の配列を掛け合わせて、微分の係数を作成する
    # 同時に定数項の係数を削除
    dcoef = (coef * dc)[:-1]
    fax, fbn = cond["f_axis"], cond["f_surf"]

    q = np.polyval(dcoef, f / (fbn - fax))
    # q = np.polyval(dcoef, f)

    qmat = np.zeros((nz, nr))
    for v, i, j in zip(q, ir, iz):
        qmat[j, i] = v

    safty = cond["resolution"].copy()
    safty["matrix"] = qmat
    cond["safty_factor"] = safty

    return cond

# 平衡計算の前処理
def equi_pre_process(cond):
    # 真空容器
    dm_vv = vmat.get_vessel(cond)
    cond["vessel"] = dm_vv

    # コイルによるフラックス
    dm_fc = cmat.get_flux_of_coil(cond)
    cond["flux_coil"] = dm_fc

    # プラズマ電流
    dm_jt = pmat.d_set_plasma_parabolic(cond)
    cond["jt"] = dm_jt

    # プラズマ電流によるフラックス
    dm_fp = pmat.cal_plasma_flux(dm_jt)
    cond["flux_jt"] = dm_fp

    # トータルのフラックス
    dm_flux = dm_add(dm_fp, dm_fc)
    cond["flux"] = dm_flux

    # 最外殻磁気面
    dm_dm = search_domain(cond)
    cond["domain"] = dm_dm

    # 領域の中心位置におけるコイルフラックスの値取得
    r = (cond["flux_coil"]["rmin"] + cond["flux_coil"]["rmax"]) / 2.0
    z = (cond["flux_coil"]["zmin"] + cond["flux_coil"]["zmax"]) / 2.0
    f = linval(r, z, cond["flux_coil"])
    ip = cond["cur_ip"]["ip"]
    # ipとfの積が正の場合は平衡が成り立たないので除外する。
    if 0 < f * ip:
        cond["domain"] = None

    return cond

# 平衡計算の後処理
def equi_post_process(cond):
    # 形状パラメータの計算（elongationなど）
    set_domain_params(cond)

    # 正規化フラックスの計算
    dm_nfl = get_normalized_flux(cond)
    cond["flux_normalized"] = dm_nfl
    
    if 'fl_pos' in cond.keys():
        pos = cond['fl_pos']
        cond['fl_val'] = {}
        for k in pos.keys():
            r, z = pos[k]
            cond['fl_val'][k] = linval(r, z, cond['flux'])

    # 圧力微分dp/dfと圧力pの計算
    dm_dp, dm_pr = get_dpress_press(cond)
    cond["diff_pre"] = dm_dp
    cond["pressure"] = dm_pr

    # ポロイダル電流微分di^2/dfとポロイダル電流の計算
    dm_di2, dm_polcur = get_di2_i(cond)
    cond["diff_i2"] = dm_di2
    cond["pol_current"] = dm_polcur

    cond = trim_values(cond)  # ip<0の時の処理
    cond = calc_beta(cond)  # ベータ値の計算
    cond = calc_safty(cond)  # safty factorの計算

    return cond

# 平衡計算(１回)
def calc_equi(cond):
    dm_jt = cond["jt"]
    dm_domain = cond["domain"]
    npr = cond["num_dpr"]
    ncu = cond["num_di2"]

    # dm_jt: プラズマ電流
    # dm_domain: 最外殻磁気面のdmat
    # npr: pressureに関するパラメータの個数
    # ncu: poloidal電流に関するパラメータの個数
    #
    # out: dmat_jt

    g = dm_array(dm_domain)

    # 1メッシュの面積を計算
    ds = g.dr*g.dz
    
    # fluxの正規化
    dm_nf = get_normalized_flux(cond)
    f = dm_nf["matrix"].reshape(-1)
    j = dm_jt["matrix"].reshape(-1)
    jtotal = np.sum(j)  # 全電流を保持しておく

    d = dm_domain["matrix"].reshape(-1)

    # 最外殻磁気面の内部のみ取り出す。
    ir = g.ir[d == 1]
    iz = g.iz[d == 1]
    r = g.r[d == 1]
    j = j[d == 1]
    f = f[d == 1]

    # 例えばパラメータ数が３の場合の時
    # (1-x^3) *a0 + (x^1-x^3)*a1 + (x^2-x^3)*a2
    # という形になることに注意すること

    # 圧力に関する行列作成
    p0 = np.array([2 * np.pi * r * (f**i - f**npr) for i in range(npr)])
    # I^2に関する行列作成
    p1 = np.array(
        [10 ** (-7) / (r + 10 ** (-7)) * (f**i - f**ncu) for i in range(ncu)]
    )
    # 結合させて転置、この時点で[point数, パラメータ数]の形
    a = np.vstack([p0, p1]).transpose()

    # 次にAbs(a x -j)を最小とするxを求めればよい。
    # A[p, n] x[n] = j[p]
    # このときのxは、At.A x = At.jを満たすxである。

    # またここでのjtの定義は、1メッシュ内に流れるトータルの電流
    # 平衡計算で用いるのは電流密度なので、メッシュサイズで割った値にする。
    m0 = np.dot(a.transpose(), a)
    m1 = np.dot(a.transpose(), j/ds)

    # m0にほんのわずかな値を加算してsingular matrixになるのを避ける
    # dd = np.min(np.abs(m0))*10**(-7)
    # m0 += np.identity(npr+ncu)*dd

    params = np.dot(np.linalg.inv(m0), m1)

    # エラー値の算出
    j0 = np.dot(a, params)  # 新しい電流
    j0 *= jtotal / np.sum(j0)  # トータルの電流値が維持されるように調整
    # この時点で、１メッシュ内に流れるトータルの電流に正規化される。

    errest = np.sum((j0 - j) ** 2) / 2
    # 評価方法はいくつか考えられる。単位メッシュ当たりのエラーに直すとか。

    # 新しい電流分布の作成
    j_new = np.zeros((g.nz, g.nr))
    for i, j, v in zip(ir, iz, j0):
        j_new[j, i] = v

    res = get_dmat_dim(dm_jt)
    res["matrix"] = j_new
    cond["error"].append(errest)
    cond["param_dp"] = params[0:npr]
    cond["param_di2"] = params[npr:]
    return res

# 平衡計算
def calc_equilibrium(condition, iteration=100, verbose=1):
    # iteration: イタレーション数。
    #   指定があった場合は、最後までイタレーションする。
    # verbose: 1:詳細表示, 0:なし

    cond = condition.copy()
    cond = equi_pre_process(cond)
    if cond["domain"] == None:
        del cond["domain"]
        cond["cal_result"] = 0
        return cond

    cond["error"] = []
    for i in range(iteration):
        # プラズマ平衡
        dm_jt = calc_equi(cond)
        cond["jt"] = dm_jt

        # プラズマ電流のトリミング
        # ip>0なら全ての領域でjt>0となるようにする。
        dm_jt2 = pmat.trim_plasma_current(cond)
        cond["jt"] = dm_jt2

        # プラズマ電流によるフラックス
        dm_fp = pmat.cal_plasma_flux(cond["jt"])
        cond["flux_jt"] = dm_fp

        # トータルのフラックス
        dm_flux = dm_add(cond["flux_jt"], cond["flux_coil"])
        cond["flux"] = dm_flux

        # 最外殻磁気面の探索
        dm_dm = search_domain(cond)
        cond["domain"] = dm_dm
        if None == cond["domain"]:
            break

        # エラー値を記録
        err = cond["error"]
        cond["iter"] = len(err)
        if 1 == verbose:
            print(i, "loss: ", err[-1])

        if len(err) <= 1:
            continue

        # iterationがデフォルト値でない場合は、設定されたという事。
        # このときは、最後までiterationする。
        if iteration < 100:
            continue

        # 前回値よりエラー値が大きくなったら終了
        # その時の配位がリミター配位なら収束しなかったとみなす。
        if err[-1] > err[-2]:
            if 0 == cond["conf_div"]:
                cond["domain"] = None
            break

        # 一番最初の変化量に対して、最新の変化量が十分小さければ終了
        v = np.abs((err[-1] - err[-2]) / (err[1] - err[0]))
        if v < 10 ** (-5):
            break

    if None == cond["domain"]:
        del cond["domain"]
        cond["cal_result"] = 0
        return cond

    cond["cal_result"] = 1

    cond = equi_post_process(cond)

    if 1 == verbose:
        pl.d_contour(dm_flux)

    return cond
