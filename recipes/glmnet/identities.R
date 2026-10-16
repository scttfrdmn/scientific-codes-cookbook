# glmnet against its own optimality conditions, closed forms and base R's fitters.
#
# glmnet solves convex problems, so the strongest check is not a band on a coefficient: it is the
# KKT conditions the solution must satisfy to be the minimiser at all. glmnet's objective is
#
#   gaussian:  1/(2n) sum (y_i - b0 - x_i'b)^2   + lambda * [(1-a)/2 |b|_2^2 + a |b|_1]
#   binomial: -1/n    sum loglik_i(b0 + x_i'b)    + same penalty
#
# with standardize=TRUE meaning the penalty is applied to b_j * s_j, where s_j is the POPULATION
# (1/n) sd of column j, and coefficients are returned on the original scale. So in original units,
# with r = y - fitted (gaussian) or y - p (binomial), the lasso KKT conditions are
#
#   active   b_j != 0:   x_j' r / n  ==  lambda * s_j * sign(b_j)
#   inactive b_j == 0:  |x_j' r / n| <=  lambda * s_j
#
# KKT tolerance, from glmnet's own stopping rule: a coordinate-descent sweep ends when the largest
# objective change from any single update, v_j * db_j^2 (v_j <= 1 on the standardised scale), is
# below thresh (gaussian: on y scaled to unit variance; binomial: times the null deviance per
# observation, <= 2 log 2 < 2). So each last update is |db_j| < sqrt(thresh) (sqrt(2 thresh) for
# binomial) and the gradient at j is off by at most the sum of the later updates times |corr| <= 1:
# <= p * sqrt(thresh), in units of s_j * sd_y. At thresh = 1e-14 that is 2e-6 for p = 20.
#
# The closed-form legs (ridge, lambda=0 vs lm/glm) run at thresh = 1e-30, where that bound
# (2e-14) is below rounding, so what is left is rounding in both solvers: bounded by
# 1e3 * kappa * eps, kappa the condition number of the standardised Gram (or Fisher) matrix.
options(warn = 1)
out <- list()
rec <- function(k, v) {
  v <- paste(vapply(v, function(e) as.character(e), ""), collapse = " ")
  out[[k]] <<- v; cat(sprintf("%s\t%s\n", k, v))
}
dump <- function() {
  writeLines(c("observable\tvalue",
               vapply(names(out), function(k) sprintf("%s\t%s", k, out[[k]]), "")),
             "/tmp/score.tsv")
}
die <- function(msg) { dump(); cat(sprintf("FAIL: %s\n", msg)); quit(status = 1) }
fe <- function(x) sprintf("%.3e", x)
# A non-finite value passes every one-sided bound (NaN > tol is FALSE), so every check goes here.
ok <- function(x, tol) is.finite(x) && x <= tol

suppressPackageStartupMessages(library(glmnet))
rec("r_version", paste0(R.version$major, ".", R.version$minor))
rec("glmnet", as.character(packageVersion("glmnet")))
rec("r_arch", R.version$arch)

THRESH <- 1e-14
EXACT <- 1e-30
EPS <- .Machine$double.eps
popsd <- function(x) sqrt(mean((x - mean(x))^2))

# ---------------------------------------------------------------------------------------------
# Planted data: n=400, p=20, neighbour-correlated columns (r ~ 0.45) on scales 0.2..5 with
# offsets -3..3 (so standardize= and the intercept both matter), a 5-variable support whose
# standardised effects are 1..2, noise sd 1.
# ---------------------------------------------------------------------------------------------
set.seed(20261016)
n <- 400L; p <- 20L
Z <- matrix(rnorm(n * p), n, p)
Z <- Z + 0.5 * Z[, c(2:p, 1)]
scales <- exp(seq(log(0.2), log(5), length.out = p))
X <- sweep(Z, 2, scales, "*") + matrix(rep(seq(-3, 3, length.out = p), each = n), n, p)
colnames(X) <- sprintf("x%02d", 1:p)
support <- c(2L, 5L, 9L, 14L, 18L)
beta <- numeric(p); beta[support] <- c(2, -1.5, 1.25, -1, 1.75) / scales[support]
y <- as.numeric(1.5 + X %*% beta + rnorm(n))
s <- apply(X, 2, popsd); sdy <- popsd(y)
Xc <- sweep(X, 2, colMeans(X)); Xs <- sweep(Xc, 2, s, "/")
G <- crossprod(Xs) / n
kG <- kappa(G, exact = TRUE)
rec("n_p", c(n, p))
rec("planted_support", paste(support, collapse = ","))
rec("gram_condition_number", sprintf("%.3f", kG))

kkt <- function(fit, r_of) {
  B <- as.matrix(fit$beta); worst <- 0; worst_inact <- 0
  for (k in seq_along(fit$lambda)) {
    b <- B[, k]; r <- r_of(fit$a0[k], b)
    g <- as.numeric(crossprod(X, r)) / n
    lam <- fit$lambda[k] * s
    act <- b != 0
    v_act <- if (any(act)) max(abs(g[act] - lam[act] * sign(b[act])) / s[act]) else 0
    v_ina <- if (any(!act)) max(pmax(abs(g[!act]) - lam[!act], 0) / s[!act]) else 0
    worst <- max(worst, v_act, v_ina); worst_inact <- max(worst_inact, v_ina)
  }
  c(worst, worst_inact)
}
gauss_r <- function(a0, b) y - a0 - as.numeric(X %*% b)
bound <- p * sqrt(THRESH)

# ---- (6) lambda_max: the first lambda is the smallest at which every coefficient is zero
fit <- glmnet(X, y, family = "gaussian", alpha = 1, control = list(thresh = THRESH))
lmax <- max(abs(as.numeric(crossprod(Xc, y - mean(y)))) / s) / n
d <- abs(fit$lambda[1] - lmax) / lmax
rec("gauss_lambda_max", sprintf("glmnet %.15g closed form %.15g rel %s", fit$lambda[1], lmax, fe(d)))
if (!ok(d, 1e-12)) die(sprintf("gaussian lambda_max off by %s relative", fe(d)))
nz1 <- sum(fit$beta[, 1] != 0)
rec("gauss_nonzero_at_lambda_max", nz1)
if (nz1 != 0) die("coefficients nonzero at lambda_max")
if (!ok(abs(fit$a0[1] - mean(y)) / abs(mean(y)), 1e-12)) die("intercept at lambda_max is not mean(y)")
fit5 <- glmnet(X, y, family = "gaussian", alpha = 0.5, control = list(thresh = THRESH))
d5 <- abs(fit5$lambda[1] - lmax / 0.5) / (lmax / 0.5)
rec("gauss_lambda_max_alpha_0.5", sprintf("rel %s", fe(d5)))
if (!ok(d5, 1e-12)) die("alpha=0.5 lambda_max is not lambda_max(alpha=1)/0.5")

# ---- (1) lasso KKT at every lambda on the path
kv <- kkt(fit, gauss_r) / sdy
rec("gauss_path_length", length(fit$lambda))
rec("gauss_kkt_max_violation", sprintf("%s (inactive %s) bound %s", fe(kv[1]), fe(kv[2]), fe(bound)))
if (!ok(kv[1], bound)) die(sprintf("gaussian lasso KKT violated: %s > %s", fe(kv[1]), fe(bound)))
# control: the same check on coefficients one lambda step off must fail by orders of magnitude
K <- ncol(fit$beta); sh <- c(2:K, K)
off <- fit; off$beta <- fit$beta[, sh]; off$a0 <- fit$a0[sh]
ko <- kkt(off, gauss_r)[1] / sdy
rec("gauss_kkt_control_shifted_lambda", sprintf("%s (%.0fx the bound)", fe(ko), ko / bound))
if (!is.finite(ko) || ko < 1000 * bound) die("KKT check does not discriminate a wrong solution")
# reported: the same path at glmnet's default thresh (1e-7); the violation should grow ~sqrt(1e7)
fd <- glmnet(X, y, family = "gaussian", alpha = 1)
kd <- kkt(fd, gauss_r)[1] / sdy
rec("gauss_kkt_default_thresh_reported", sprintf("%s (%.0fx the 1e-14 run; sqrt(1e7) = 3162)",
                                                 fe(kd), kd / kv[1]))

# ---- (2) ridge == closed form. On standardised X and y scaled to unit population sd, glmnet's
# ridge solves (X'X/n + lambda I) b = X'y/n.
ys <- (y - mean(y)) / sdy
lam_r <- 10^seq(1, -3, length.out = 25)
xtol <- 1e3 * kG * EPS
fr <- glmnet(Xs, ys, family = "gaussian", alpha = 0, lambda = lam_r, standardize = FALSE,
             intercept = FALSE, control = list(thresh = EXACT))
h <- crossprod(Xs, ys) / n
rd <- max(vapply(seq_along(lam_r), function(k)
  max(abs(as.numeric(fr$beta[, k]) - as.numeric(solve(G + lam_r[k] * diag(p), h)))), 0))
rec("ridge_vs_closed_form_max_abs", sprintf("%s over %d lambdas, bound %s", fe(rd), length(lam_r), fe(xtol)))
if (!ok(rd, xtol)) die(sprintf("ridge differs from (X'X/n + lambda I)^-1 X'y/n by %s", fe(rd)))
# On the ORIGINAL y scale the reported lambda is in y units, so the ridge glmnet solves is
# (X'X/n + (lambda/sd_y) I) b = X'y/n. Asserted; the naive reading is reported beside it.
fro <- glmnet(Xs, y, family = "gaussian", alpha = 0, lambda = lam_r, standardize = FALSE,
              control = list(thresh = EXACT))
h2 <- crossprod(Xs, y - mean(y)) / n
rdev <- function(f) max(vapply(seq_along(lam_r), function(k)
  max(abs(as.numeric(fro$beta[, k]) - as.numeric(solve(G + f(lam_r[k]) * diag(p), h2)))) / sdy, 0))
rdo <- rdev(function(l) l / sdy); rdn <- rdev(function(l) l)
rec("ridge_raw_y_lambda_over_sdy", fe(rdo))
rec("ridge_raw_y_naive_lambda_reported", fe(rdn))
if (!ok(rdo, xtol)) die("ridge on raw y does not solve with lambda/sd(y)")

# ---- (3) lambda = 0 equals lm() OLS (p < n), standardize=TRUE on the raw X
f0 <- glmnet(X, y, family = "gaussian", alpha = 1, lambda = c(1, 0.1, 0.01, 0),
             control = list(thresh = EXACT))
ols <- coef(lm(y ~ X))
b0 <- as.numeric(coef(f0)[, 4])
od <- max(abs(b0[-1] - ols[-1]) * s) / sdy
oi <- abs(b0[1] - ols[1]) / sdy
rec("lambda0_vs_lm", sprintf("max |db|*s/sd_y %s, intercept %s, bound %s", fe(od), fe(oi), fe(xtol)))
if (!ok(od, xtol)) die(sprintf("lambda=0 lasso differs from lm() by %s", fe(od)))

# ---- (4) planted support from cv.glmnet with a fixed foldid; a permuted-y null selects nothing
foldid <- rep_len(1:10, n)[sample.int(n)]
cv <- cv.glmnet(X, y, foldid = foldid, alpha = 1, relax = TRUE, control = list(thresh = THRESH))
cv2 <- cv.glmnet(X, y, foldid = foldid, alpha = 1, relax = TRUE, control = list(thresh = THRESH))
if (!identical(cv$cvm, cv2$cvm)) die("cv.glmnet with a fixed foldid is not deterministic")
rec("cv_deterministic", "identical cvm over two runs")
nzv <- function(b) which(as.numeric(b)[-1] != 0)
B <- as.matrix(cv$glmnet.fit$beta)
first <- apply(B != 0, 1, function(v) if (any(v)) which(v)[1] else Inf)
entry <- sort(order(first)[1:5])
rec("first_five_to_enter", paste(entry, collapse = ","))
if (!identical(entry, support)) die("the first five variables to enter are not the planted support")
sr <- nzv(coef(cv, s = "lambda.1se", gamma = "gamma.1se"))
rec("relaxed_1se_support", paste(sr, collapse = ","))
if (!identical(sr, support)) die("relaxed lasso at lambda.1se does not recover the support exactly")
s1 <- nzv(coef(cv$glmnet.fit, s = cv$lambda.1se))
rec("plain_lasso_1se_support_reported", paste(s1, collapse = ","))
if (!all(support %in% s1)) die("plain lasso at lambda.1se misses a planted variable")
yperm <- y[sample.int(n)]
cvn <- cv.glmnet(X, yperm, foldid = foldid, alpha = 1, control = list(thresh = THRESH))
n1 <- length(nzv(coef(cvn, s = "lambda.1se")))
nm <- length(nzv(coef(cvn, s = "lambda.min")))
rec("null_1se_selected", n1)
rec("null_min_selected_reported", nm)
if (n1 != 0) die(sprintf("permuted-y null selected %d variables at lambda.1se", n1))

# ---- (5) binomial: lambda_max, KKT on the deviance gradient, lambda = 0 equals glm()
eta <- as.numeric(-0.3 + Xc %*% (beta * 0.8))
yb <- rbinom(n, 1, 1 / (1 + exp(-eta)))
rec("binomial_cases", sprintf("%d of %d", sum(yb), n))
fb <- glmnet(X, yb, family = "binomial", alpha = 1, control = list(thresh = THRESH))
lmb <- max(abs(as.numeric(crossprod(Xc, yb - mean(yb)))) / s) / n
db <- abs(fb$lambda[1] - lmb) / lmb
rec("binom_lambda_max", sprintf("rel %s", fe(db)))
if (!ok(db, 1e-12)) die("binomial lambda_max off")
if (sum(fb$beta[, 1] != 0) != 0) die("binomial coefficients nonzero at lambda_max")
binom_r <- function(a0, b) yb - 1 / (1 + exp(-(a0 + as.numeric(X %*% b))))
kb <- kkt(fb, binom_r)
bb_bound <- p * sqrt(2 * THRESH)
rec("binom_path_length", length(fb$lambda))
rec("binom_kkt_max_violation", sprintf("%s (inactive %s) bound %s", fe(kb[1]), fe(kb[2]), fe(bb_bound)))
if (!ok(kb[1], bb_bound)) die(sprintf("binomial lasso KKT violated: %s", fe(kb[1])))
ib <- max(abs(vapply(seq_along(fb$lambda), function(k) mean(binom_r(fb$a0[k], fb$beta[, k])), 0)))
rec("binom_intercept_score", sprintf("max |mean(y - p)| %s", fe(ib)))
if (!ok(ib, bb_bound)) die("binomial intercept score equation sum(y - p) = 0 violated")
fb0 <- glmnet(X, yb, family = "binomial", alpha = 1, lambda = c(0.1, 0.01, 0.001, 0),
              control = list(thresh = EXACT))
gm <- glm(yb ~ X, family = binomial, control = glm.control(epsilon = 1e-15, maxit = 100))
pg <- fitted(gm); Xs1 <- cbind(1, Xs)
kF <- kappa(crossprod(Xs1 * sqrt(pg * (1 - pg))) / n, exact = TRUE)
btol <- 1e3 * kF * EPS
bb <- as.numeric(coef(fb0)[, 4]); g0 <- coef(gm)
gd <- max(abs(bb[-1] - g0[-1]) * s)
rec("binom_fisher_condition_number", sprintf("%.3f", kF))
rec("binom_lambda0_vs_glm", sprintf("max |db|*s %s, intercept %s, bound %s", fe(gd),
                                    fe(abs(bb[1] - g0[1])), fe(btol)))
if (!ok(gd, btol)) die(sprintf("lambda=0 logistic differs from glm() by %s", fe(gd)))

dump()
cat("GLMNET IDENTITIES OK\n")
