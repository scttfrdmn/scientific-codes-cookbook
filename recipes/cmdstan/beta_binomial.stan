// A conjugate Beta-Binomial model, chosen because its posterior is known in closed form:
// theta ~ Beta(a, b) with y successes in N trials gives theta | y ~ Beta(a + y, b + N - y)
// exactly. That makes every check below a comparison against an analytical reference rather
// than a band on whatever the sampler happened to return.
data {
  int<lower=0> N;            // trials
  int<lower=0, upper=N> y;   // successes
  real<lower=0> a;           // prior shape
  real<lower=0> b;           // prior shape
}
parameters {
  real<lower=0, upper=1> theta;
}
model {
  theta ~ beta(a, b);
  y ~ binomial(N, theta);
}
