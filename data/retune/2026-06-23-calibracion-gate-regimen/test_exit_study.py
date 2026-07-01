import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import exit_study as ex

def test_ladder_cobra_todos_los_targets():
    # entry=100, high llega a 200 (>+90% → 4 targets), close=200 (runner +100%)
    r = ex.ladder_return(100.0, [200.0], [99.0], 200.0)
    # realized = .25*.15+.25*.30+.20*.50+.15*.90 = .3475 ; runner=.15*1.0=.15
    assert abs(r - 0.4975) < 1e-9

def test_ladder_catastrofe():
    # entry=100, ningún target (high 105<115), low toca 50 (−50%) → −0.50
    assert ex.ladder_return(100.0, [105.0], [50.0], 104.0) == -0.50

def test_ladder_runner_solo():
    # entry=100, ningún target, no catástrofe (low 95), close 110 → runner 100% × +10% = 0.10
    assert abs(ex.ladder_return(100.0, [108.0], [95.0], 110.0) - 0.10) < 1e-9
