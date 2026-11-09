// Every loop the extension module has (pyext.rs py_loops!): `name(arg: type, ...) => the loop;`. The doc of each
// Python function = these types (native.py makes plain copies of those kinds when an array can't be read as is).
py_loops! {
    old: [OLD_STIR, OLD_WINNER, OLD_KEY];
    // ---- the piano roll's note pictures (draw.rs)
    paint_order(notes: Nd<i64, 2>, by_start: &[i64], rank: &[i64], nranks: i64, pics: &[Flag], cols: &[i64],
                picgroup: i64, ghost: i64, s: &mut [i64], e: &mut [i64], key: &mut [i16], color: &mut [i32],
                first: &mut [i64]) => crate::draw::paint_order;
    screen_count(s: &[i64], first: &[i64], b0: i64, b1: i64, t_lo: f64, t_hi: f64) => crate::draw::screen_count;
    order_screen(s: &[i64], e: &[i64], key: &[i16], color: &[i32], first: &[i64], b0: i64, b1: i64, t_lo: f64,
                 t_hi: f64, ax: f64, bx: f64, kb: f64, w: f64, shown: &[Flag], clip: bool, c0: f64, c2: f64,
                 out: NdMut<i64, 2>) => crate::draw::order_screen;
    order_rects(s: &[i64], e: &[i64], key: &[i16], color: &[i32], first: &[i64], b0: i64, b1: i64, t_lo: f64,
                t_hi: f64, ax: f64, bx: f64, kb: f64, w: f64, shown: &[Flag], clip: bool, c0: f64, c2: f64,
                left: i64, right: i64, keys: i64, row0: &[i64], row1: &[i64], out: NdMut<i64, 2>)
                => crate::draw::order_rects;
    liquify_rects(on: &[i64], slot: &[i64], rank: &[i64], colour: &[i64], x0: &[i64], x1: &[i64], key: &[i64],
                  vq: &[i64], out: NdMut<i64, 2>) => crate::draw::liquify_rects;
    last_on_pixels(x0: &mut [i64], x1: &mut [i64], key: &[i64], color: &[i64], left: i64, right: i64, keys: i64,
                   row0: &[i64], row1: &[i64], out: NdMut<i64, 2>) => crate::draw::last_on_pixels;
    note_top(top_px: &mut [i64], half: &mut [u8], kb: i64, top: i64, w: i64, h: i64, x0: &[i64], y0: &[i64],
             x1: &[i64], y1: &[i64], color: &[i64], shade: bool) => crate::draw::note_top;
    paint_notes(img: NdMut<u8, 2>, kb: i64, top: i64, w: i64, h: i64, x0: &[i64], y0: &[i64], x1: &[i64],
                y1: &[i64], color: &[i64], rgb: Nd<u8, 3>, shade: bool) => crate::draw::paint_notes;
    // ---- the note engine (engine.rs)
    take2(a: &mut [i64], b: &mut [i64]) => crate::engine::take2;
    overlap_order(notes: Nd<i64, 2>, group: &mut [i64], order: &mut [i64]) => crate::engine::overlap_order;
    overlap_sweep(notes: Nd<i64, 2>, order: &[i64], group: &[i64], out: NdMut<i64, 2>) => crate::engine::overlap_sweep;
    note_stretches(notes: Nd<i64, 2>, by_key: bool, owner: i64, order: &[i64], out: NdMut<i64, 2>)
                   => crate::engine::note_stretches;
    radix_order(k: &mut [i64], bits: i64, order: &mut [i64], o2: &mut [i64]) => crate::engine::radix_order;
    ring_runs(s: &[i64], e: &[i64], k: &[i64], order: &[i64], gap: f64, big: i64, out: NdMut<i64, 2>)
              => crate::engine::ring_runs;
    ring_runs_int(s: &[i64], e: &[i64], k: &[i64], order: &[i64], gap: i64, big: i64, out: NdMut<i64, 2>)
                  => crate::engine::ring_runs_int;
    clash_pairs(items: Nd<i64, 2>, n: i64, most: i64) => crate::engine::clash_pairs;
    clash_levels(rows: Nd<i64, 2>, offs: &[i64], out: &mut [i64]) => crate::engine::clash_levels;
    any_overlap(notes: Nd<i64, 2>, g: &[i64], ng: i64) => crate::engine::any_overlap;
    // ---- turned / slanted / taller pasted notes (turn.rs)
    turn_sample(flat: &[f64], skey: &[i64], send: &[i64], g: &[f64], lo_k: i64, hi_k: i64, n: i64, many: bool)
                -> arrays => crate::turn::turn_sample;
    turn_layers(s: &[i64], e: &[i64], k: &[i64], first: &[u8], over: &[u8], lay: &mut [i64])
                => crate::turn::turn_layers;
    // ---- Recolour / Stir / Liquify (recolour.rs)
    stir_nearest(keys: &[i64], first: &[i64], last: &[i64], line: &[f64], ms: &[f64], nk: &[i64], nm: &[f64],
                 o: &[i64], base: f64, top: f64, gap: f64, mid: &[f64], key: &[f64], own: bool, out: &mut [i64],
                 many: bool) => crate::recolour::stir_nearest;
    stir_wrap(keys: &[i64], a: &[f64], b: &[f64], mid: &[f64], key: &[f64], out: &mut [f64], many: bool)
              => crate::recolour::stir_wrap;
    liquify_sample(a: Nd<f64, 3>, x: &[f64], y: &[f64], out: NdMut<f64, 2>, many: bool)
                   => crate::recolour::liquify_sample;
    warp_newton(p: Nd<f64, 3>, px: &[f64], py: &[f64], kind: i64, lx: &[f64], ly: &[f64], sx: &[f64], sy: &[f64],
                bounds: Nd<f64, 2>,
                has_b: bool, steps: i64, give_step: i64, give_far: f64, bx: &mut [f64], by: &mut [f64],
                best: &mut [f64], many: bool) => crate::recolour::warp_newton;
    warp_soft(at: Nd<i64, 2>, idx: &[i64], x: &[f64], y: &[f64], a: Nd<f64, 3>, c: &[f64], fp: &[f64],
              ring: Nd<f64, 2>, tstart: &[i64], ta: &[f64], tb: &[f64], keys: &[i64], first: &[i64], last: &[i64],
              line: &[f64], ms: &[f64], o: &[i64], base: f64, top: f64, gap: f64, now: &[i64], out: &mut [i64],
              many: bool) => crate::recolour::warp_soft;
    dice_mix(seed: u64, c0: &[u64], c1: &[u64], c2: &[u64], c3: &[u64], out: &mut [f64]) => crate::recolour::dice_mix;
    recolour_apart(order: &[i64], ss: &[i64], e: &[i64], old: &[i64], new: &mut [i64], pick: &[f64], bstart: &[i64],
                   bsize: &[i64], nc: i64) => crate::recolour::recolour_apart;
    // ---- MIDI bytes, Image to notes, areas, Nested fill (misc.rs)
    take_bytes(out: &mut [u8]) => crate::misc::take_bytes;
    midi_events(notes: Nd<i64, 2>, rows: &[i64], order: &[i64], ch: i64, most: i64) => crate::misc::midi_events;
    midi_track(notes: Nd<i64, 2>, rows: &[i64], ch: i64, most: i64, bits: i64) => crate::misc::midi_track;
    nearest(lab: Nd<f64, 2>, cen: Nd<f64, 2>, out: &mut [i64]) => crate::misc::nearest;
    spread_line(a0: Nd<f64, 3>, free: Nd<f64, 2>, pal: Nd<f64, 2>, pal_lab: Nd<f64, 2>, strength: f64,
                idx: NdMut<i64, 2>, err: NdMut<f64, 3>, line: i64, lab: Nd<f64, 2>, vals: NdMut<f64, 2>,
                clipped: NdMut<f64, 2>) => crate::misc::spread_line;
    area_spots(x: &[f64], y: &[f64], levels: &[f64], start: &[i64], off: &[i64], lab: &[i64], outside: i64,
               edge: &[i64], ex: &[f64], ey: &[f64], es: &[f64], x0: &[f64], y0: &[f64], x1: &[f64], y1: &[f64],
               eps: f64, out: &mut [i64]) => crate::misc::area_spots;
    smooth_sor(flat: &mut [f64], red: &[i64], black: &[i64], wide: i64, om: f64, times: i64)
               => crate::misc::smooth_sor;
    dc_block(x: NdMut<f32, 2>, c: &[f64], z: NdMut<f64, 2>) => crate::misc::dc_block;
    // ---- Hz bass (hz.rs)
    wave_fill(starts: &[i64], a: i64, b: i64, sq0: &[i64], wo: &[f64], out: &mut [f64]) => crate::hz::wave_fill;
    note_ends(s: &[i64], e: &[i64], k: &[i64], stick: bool, mode: i64, beats: f64, pct: f64, pct_int: bool,
              wv: &[f64], wf: f64, big: i64, res: &mut [i64]) => crate::hz::note_ends;
    hz_expand(runs_sel: &[i64], copy_sel: &[i64], scale_c: &[f64], n_run: &[i64], off_run: &[i64], prev_run: &[i64],
              moves_run: &[Flag], pos_len: i64, x: f64, off_pitch: f64, opf: &[f64], vf: &[f64], w_: &[f64],
              s_: &[f64]) -> arrays => crate::hz::hz_expand;
    hz_late(part: &[i64], number: &[i64], src: &[i64], w: &[f64], s: &[f64], hits: &[f64], has_trace: bool, x: f64,
            slant: &[f64], groups: &[i64], noisy_f: &[f64], np_: i64) -> arrays => crate::hz::hz_late;
    hz_order(wg: &[i64], out: &mut [i64]) => crate::hz::hz_order;
    hz_finish(part: &[i64], number: &[i64], src: &[i64], w: &[f64], st0: &[f64], late: &[f64], nsrc: &[f64],
              noisy_p: &[Flag], place: &[i64], drawn: &[f64], until_p: &[f64], gains_sel: &[f64], has_loud: bool,
              has_wah: bool, hv: &[Flag], vol_f: &[f64], lev: &[f64], oct_f: &[f64], own_f: &[f64],
              layer_f: &[f64], lim_f: &[f64], loud_sw: &[f64], c1: &[f64], tf: &[f64], soft_thr: f64)
              -> arrays => crate::hz::hz_finish_f;
    hz_finish_i(part: &[i64], number: &[i64], src: &[i64], w: &[f64], st0: &[f64], late: &[f64], nsrc: &[f64],
                noisy_p: &[Flag], place: &[i64], drawn: &[f64], until_p: &[f64], gains_sel: &[f64], has_loud: bool,
                has_wah: bool, hv: &[Flag], vol_f: &[f64], lev: &[f64], oct_f: &[f64], own_f: &[f64],
                layer_f: &[f64], lim_f: &[i64], loud_sw: &[f64], c1: &[f64], tf: &[f64], soft_thr: f64)
                -> arrays => crate::hz::hz_finish_i;
    // ---- Keep timing's file tempos (tempo.rs)
    gap_ticks(a: i64, e: i64, ts: &[i64], bs: &[i64], drawn: &[i64], was: &[i64], u: i64, most: i64)
              -> arrays => crate::tempo::gap_ticks;
    gap_room(a: i64, e: i64, ts: &[i64], bs: &[i64]) => crate::tempo::gap_room;
    gap_ticks_into(a: i64, e: i64, ts: &[i64], bs: &[i64], drawn: &[i64], was: &[i64], u: i64, most: i64,
                   n: &mut [i64], total: &mut [i64], k: &mut [i64], dense: &mut [i64])
                   => crate::tempo::gap_ticks_into;
    gap_plan(j: i64, last: i64, a: i64, n: &[i64], total: &[i64], k: &mut [i64], drawn: &[i64], pref: &[i64],
             every: i64, most: i64, safe_reach: i64, pair_reach: i64, lens: &mut [i64], vals: &mut [i64], pos: i64,
             got: &mut [i64]) -> arrays => crate::tempo::gap_plan_t;
    // ---- Load MIDI's byte loops (midi_scan.rs)
    midi_walk(b: &[u8], p: i64, end: i64, final_: bool, rest: i64, st: &mut [i64], cnt: &mut [i64])
              => crate::midi_scan::walk;
    midi_rewrite(b: &[u8], p: i64, end: i64, final_: bool, bp_n: &[i64], bp_f: &[i64], use_map: bool, drop: &[u8],
                 ins_t: &[i64], ins_at: &[i64], ins_b: &[u8], out: &mut [u8], o: i64, st: &mut [i64], base: i64,
                 skip: &[i64], chan: i64, keep: i64, meta: i64, res: &mut [i64]) => crate::midi_scan::rewrite;
    midi_note_ticks(b: &[u8], p: i64, end: i64, final_: bool, mark: &mut [u8], base: i64, st: &mut [i64])
                    => crate::midi_scan::note_ticks;
    midi_busy_hits(b: &[u8], p: i64, end: i64, base: i64, skip: &[i64], t_stop: i64, st: &mut [i64],
                   cnt: &mut [i64], since: &mut [i64], lane_at: &[i64], own_s: &[i64], own_e: &[i64],
                   own_top: &[i64], hit: &mut [u8]) => crate::midi_scan::busy_hits;
    midi_gather(b: &[u8], p: i64, end: i64, base: i64, skip: &[i64], t0: i64, t1: i64, k0: i64, k1: i64,
                chans: &[u8], st: &mut [i64], old: &mut [i64], newer: &mut [i64], head: &mut [i64],
                tail: &mut [i64], nxt: &mut [i64], o_s: &mut [i64], o_e: &mut [i64], o_k: &mut [i64],
                o_v: &mut [i64], o_on: &mut [i64], o_off: &mut [i64], o_was: &mut [u8]) => crate::midi_scan::gather;
    // ---- Load MIDI's index and pictures (midi_index.rs)
    midi_index(b: &[u8], p: i64, end: i64, at: i64, g_: i64, ix: &mut [i64], q_hd: &mut [i64], q_tl: &mut [i64],
               q_rl: &mut [i64], q_pos: &mut [i64], q_vel: &mut [i64], opened: &mut [i64], pt: &mut [i64],
               pn: &mut [i64], pw: &mut [i64], cp_off: &mut [i64], cp_tick: &mut [i64], cp_run: &mut [i64],
               cp_ent: &mut [i64], e_q: &mut [i64], e_cnt: &mut [i64], e_end: &mut [i64], e_vel: &mut [i64],
               e_nx: &mut [i64], cov: NdMut<i32, 2>, edg: NdMut<u8, 2>, chn: &mut [i64], m_tick: &mut [i64],
               m_type: &mut [i64], m_len: &mut [i64], m_data: NdMut<u8, 2>, c_tick: &mut [i64], c_code: &mut [i32],
               ccn: &mut [i64]) -> arrays => crate::midi_index::index;
    midi_close(ix: &mut [i64], g_: i64, q_hd: &[i64], q_rl: &mut [i64], opened: &[i64], pt: &[i64], pn: &[i64],
               pw: &[i64], e_end: &mut [i64], e_nx: &[i64], cov: NdMut<i32, 2>, edg: NdMut<u8, 2>)
               => crate::midi_index::close;
    midi_grid(cov: NdMut<i32, 2>, edg: NdMut<u8, 2>, glo: i64, ghi: i64, klo: i64, khi: i64, cnt: NdMut<i32, 2>,
              top: NdMut<i32, 2>, etop: NdMut<i32, 2>, track: i64, pic: NdMut<u8, 2>, pf: i64)
              => crate::midi_index::grid;
    midi_halve(cnt: Nd<i32, 2>, top: Nd<i32, 2>, cnt2: NdMut<i32, 2>, top2: NdMut<i32, 2>) => crate::midi_index::halve;
    midi_cover(b: &[u8], p: i64, end: i64, base: i64, skip: &[i64], st: &mut [i64], cnt_q: &mut [i64],
               first_q: &mut [i64], vel_q: &mut [i64], t1: i64, close_at: i64, ax: f64, bx: f64, w: i64, k_lo: i64,
               colour: Nd<i64, 2>, tab: NdMut<i64, 2>, nx: NdMut<i64, 2>, filled: &mut [i64], marks: NdMut<u8, 2>,
               sq: &mut [i64], sa: &mut [i64], sb: &mut [i64], sv: &mut [i64]) => crate::midi_index::cover;
    midi_merge_loud(tab: NdMut<i64, 2>, marks: NdMut<u8, 2>, ttab: NdMut<i64, 2>, tmarks: NdMut<u8, 2>,
                    tnx: NdMut<i64, 2>, tfilled: &mut [i64], ghost: i64) => crate::midi_index::merge_loud;
    midi_cover_many(faint: &[Flag], klo: &[i64], khi: &[i64], starts: &[i64], ends: &[i64], e0s: &[i64],
                    e1s: &[i64], cp_tick: &[i64], cp_run: &[i64], limits: &[i64], lasts: &[i64], buf: &[u8],
                    pos: &[i64], eq: &[i16], ec: &[i64], ev: &[u8], skip: &[i64], t1: i64, ax: f64, bx: f64, w: i64,
                    k_lo: i64, normal: Nd<i64, 2>, behind: Nd<i64, 2>, tab: NdMut<i64, 2>, nx: NdMut<i64, 2>,
                    filled: &mut [i64], marks: NdMut<u8, 2>, sq: &mut [i64], sa: &mut [i64], sb: &mut [i64],
                    sv: &mut [i64], cnt_q: &mut [i64], first_q: &mut [i64], vel_q: &mut [i64], st: &mut [i64],
                    covered: &mut [i64]) => crate::midi_index::cover_many;
    loaded_pixels(img: NdMut<u8, 3>, x0: i64, y0: i64, tab: Nd<i64, 2>, marks: Nd<u8, 2>, c0: i64, c1: i64, kb: i64,
                  row0: &[i64], row1: &[i64], rgb: Nd<u8, 3>) => crate::midi_index::loaded_pixels;
    all_tops(top: NdMut<i32, 2>, data: &[u8], offs: &[i64], nrows: &[i64], nbytes: &[i64], pklo: &[i64],
             glo: &[i64], pf: &[i64], cols: &[i64], used: &[Flag], uoff: &[i64], ids: &[i64], x0: i64, tpp: f64,
             g_: i64) => crate::midi_index::all_tops;
    // ---- playing a loaded MIDI (play.rs)
    play_read(b: &[u8], at: &[i64], size: &[i64], t1: i64, tick: &mut [i64], run: &mut [i64], nxt: &mut [i64],
              used: &mut [i64], kn: &[i32], out_t: &mut [i64], out_m: &mut [i32], out_k: &mut [i32], n: i64,
              base: &[i64], sk: &[i64], sk_i: &mut [i64], sk_end: &[i64], count: bool, opened: &mut [i64],
              slot: &[i64]) => crate::play::read;
    play_programs(b: &[u8], p: i64, end: i64, st: &mut [i64]) => crate::play::programs;
    play_order(ticks: &[i64], ms: &[i32], ks: &[i32], base: i64, span: i64, out_t: &mut [i64], out_m: &mut [i32],
               out_k: &mut [i32]) => crate::play::order;
    play_order_count(ticks: &[i64], base: i64, count: &mut [i64]) => crate::play::order_count;
    play_order_place(ticks: &[i64], ms: &[i32], ks: &[i32], base: i64, at: &mut [i64], out_t: &mut [i64],
                     out_m: &mut [i32], out_k: &mut [i32]) => crate::play::order_place;
    play_hist(ms: &[i32], trk: &[i32], i0: i64, i1: i64, ticks: &[i64], t_late: i64, muted: &[u16], tport: &[i64],
              ports: i64, synths: i64, h: NdMut<i64, 2>) => crate::play::hist;
    play_stamp(ms: &[i32], trk: &[i32], i0: i64, i1: i64, ticks: &[i64], muted: &[u16], tport: &[i64], mt: &[i64],
               ms_: &[f64], mspt: &[f64], base: f64, rate: f64, least: f64, fr: &mut [u64], out: &mut [u32],
               tr: &mut [u16], counts: &mut [i64], cp: &mut [u32], tab_key: &mut [u32], tab_gen: &mut [i64],
               tab_at: &mut [i64], head: &mut [i64], tail: &mut [i64], val: &mut [i64], nxt: &mut [i64],
               st: &mut [i64]) => crate::play::stamp;
    play_condense(ts: &[i64], ms: &[i32], ks: &[i32], tport: &[i64], n: i64, out_t: &mut [i64], out_m: &mut [u32],
                  out_tr: &mut [u16], out_cp: &mut [u32], tab_key: &mut [u32], tab_gen: &mut [i64],
                  tab_at: &mut [i64], counts: &mut [i64], last_on: &mut [i64]) => crate::play::condense;
    play_stamp_pre(ts: &[i32], ms: &[u32], trs: &[u16], cps: &[u32], t0: i64, i0: i64, i1: i64, mt: &[i64],
                   ms_: &[f64], mspt: &[f64], base: f64, rate: f64, least: f64, fr: &mut [u64], out: &mut [u32],
                   tr: &mut [u16], cp: &mut [u32], counts: &mut [i64], dry: bool, head: &mut [i64],
                   tail: &mut [i64], val: &mut [i64], nxt: &mut [i64], st: &mut [i64]) => crate::play::stamp_pre;
    play_emit(ms: &[i32], trk: &[i32], i0: i64, i1: i64, ticks: &[i64], t_late: i64, muted: &[u16], tport: &[i64],
              ports: i64, synths: i64, thr: &[i64], room: &mut [i64], out: NdMut<u8, 2>, lens: &mut [i64],
              three: bool, head: &mut [i64], tail: &mut [i64], val: &mut [i64], nxt: &mut [i64], st: &mut [i64])
              => crate::play::emit;
    // ---- Rendered ahead's pieces of a loaded MIDI (piece.rs)
    piece_track(b: &[u8], base: i64, tick: i64, run: i64, t_pre: i64, t_end: i64, sk: &[i64], cnt: &mut [i64],
                vel: &mut [i64], ontk: &mut [i64], muted: i64, newch: i64, last: i64, out: &mut [u8], t_on: i64,
                opn: &mut [i64], port: i64, base_t: i64, bars: &[i64], bar_ch: &[u8], ev_key: &mut [i64],
                ev_sig: &mut [u8], nev: &mut [i64]) -> arrays => crate::piece::piece_track;
    piece_held_ons(b: &[u8], base: i64, tick: i64, run: i64, t_stop: i64, sk: &[i64], muted: i64, need: &[i64],
                   off: &[i64], tk: &mut [i64], vl: &mut [i64], pos: &mut [i64]) => crate::piece::held_ons;
    piece_held_head(tk: &[i64], qs: &[i64], vl: &[i64], newch: i64, last: i64, out: &mut [u8]) -> arrays
                    => crate::piece::held_head;
    piece_note_ons(b: &[u8], base: i64, tick: i64, run: i64, t_stop: i64, sk: &[i64], muted: i64, want: &[i8],
                   out_t: &mut [i64], out_q: &mut [i64], out_v: &mut [i64]) => crate::piece::note_ons;
    piece_runs_sort(key: &mut [i64], sig: &mut [u8], cnt: &mut [i64], n: i64, tk: &mut [i64], ts: &mut [u8],
                    tc: &mut [i64], bits: i64) => crate::piece::runs_sort;
    piece_runs_merge(ak: &[i64], asg: &[u8], ac: &[i64], na: i64, bk: &[i64], bsg: &[u8], bc: &[i64], nb: i64,
                     ok: &mut [i64], osg: &mut [u8], oc: &mut [i64]) => crate::piece::runs_merge;
    piece_counted_track(key: &[i64], sig: &[u8], cnt: &[i64], n: i64, port: i64, rel: i64, out: &mut [u8])
                        -> arrays => crate::piece::counted_track;
    piece_order_ok(key: &[i64], sig: &[u8], cnt: &[i64], n: i64, bar_ch: &[u8]) => crate::piece::order_ok;
    piece_open_at(key: &[i64], sig: &[u8], cnt: &[i64], n: i64, lim: i64, wlim: i64, take: &mut [i64])
                  => crate::piece::open_at;
}
