from __future__ import absolute_import, division, print_function

import warnings

import pyro
import pyro.poutine as poutine
from pyro.distributions.util import is_identically_zero
from pyro.infer.elbo import ELBO
from pyro.poutine.util import prune_subsample_sites
from pyro.util import check_model_guide_match, check_site_shape, is_nan
from pyro.infer.util import MultiViewTensor, n_compatible_indices


def compute_site_log_r(model_trace, guide_trace, target_site, target_shape):
    log_r = MultiViewTensor()
    stacks = model_trace.graph["iarange_info"]['iarange_stacks']
    for name, model_site in model_trace.nodes.items():
        if model_site["type"] == "sample":
            log_r_term = model_site["batch_log_pdf"]
            if not model_site["is_observed"]:
                guide_site = guide_trace.nodes[name]
                guide_log_pdf, _, _ = guide_site["score_parts"]
                log_r_term -= guide_log_pdf
            log_r_term = MultiViewTensor(log_r_term.detach())
            dims_to_keep = n_compatible_indices(stacks, name, target_site)
            summed_log_r_term = log_r_term.sum_leftmost_all_but(dims_to_keep)
            log_r.add(summed_log_r_term)

    return log_r.contract(target_shape)


class Trace_ELBO(ELBO):
    """
    A trace implementation of ELBO-based SVI
    """

    def _get_traces(self, model, guide, *args, **kwargs):
        """
        runs the guide and runs the model against the guide with
        the result packaged as a trace generator
        """
        for i in range(self.num_particles):
            guide_trace = poutine.trace(guide).get_trace(*args, **kwargs)
            model_trace = poutine.trace(poutine.replay(model, guide_trace)).get_trace(*args, **kwargs)

            check_model_guide_match(model_trace, guide_trace)
            guide_trace = prune_subsample_sites(guide_trace)
            model_trace = prune_subsample_sites(model_trace)

            model_trace.compute_batch_log_pdf()
            guide_trace.compute_score_parts()
            for site in model_trace.nodes.values():
                if site["type"] == "sample":
                    check_site_shape(site, self.max_iarange_nesting)
            for site in guide_trace.nodes.values():
                if site["type"] == "sample":
                    check_site_shape(site, self.max_iarange_nesting)

            yield model_trace, guide_trace

    def loss(self, model, guide, *args, **kwargs):
        """
        :returns: returns an estimate of the ELBO
        :rtype: float

        Evaluates the ELBO with an estimator that uses num_particles many samples/particles.
        """
        elbo = 0.0
        for model_trace, guide_trace in self._get_traces(model, guide, *args, **kwargs):
            elbo_particle = (model_trace.log_pdf() - guide_trace.log_pdf()).item()
            elbo += elbo_particle / self.num_particles

        loss = -elbo
        if is_nan(loss):
            warnings.warn('Encountered NAN loss')
        return loss

    def loss_and_grads(self, model, guide, *args, **kwargs):
        """
        :returns: returns an estimate of the ELBO
        :rtype: float

        Computes the ELBO as well as the surrogate ELBO that is used to form the gradient estimator.
        Performs backward on the latter. Num_particle many samples are used to form the estimators.
        """
        elbo = 0.0
        # grab a trace from the generator
        for model_trace, guide_trace in self._get_traces(model, guide, *args, **kwargs):
            elbo_particle = 0
            surrogate_elbo_particle = 0

            # compute elbo and surrogate elbo
            for name, model_site in model_trace.nodes.items():
                if model_site["type"] == "sample":
                    model_log_pdf = model_site["log_pdf"]
                    if model_site["is_observed"]:
                        elbo_particle = elbo_particle + model_log_pdf.item()
                        surrogate_elbo_particle = surrogate_elbo_particle + model_log_pdf
                    else:
                        guide_site = guide_trace.nodes[name]
                        guide_log_pdf, score_function_term, entropy_term = guide_site["score_parts"]

                        elbo_particle = elbo_particle + model_log_pdf - guide_log_pdf.sum()
                        surrogate_elbo_particle = surrogate_elbo_particle + model_log_pdf

                        if not is_identically_zero(entropy_term):
                            surrogate_elbo_particle -= entropy_term.sum()

                        if not is_identically_zero(score_function_term):
                            log_r_site = compute_site_log_r(model_trace, guide_trace, name, guide_log_pdf.shape)
                            surrogate_elbo_particle = surrogate_elbo_particle + (log_r_site * score_function_term).sum()

            elbo += elbo_particle / self.num_particles

            # collect parameters to train from model and guide
            trainable_params = set(site["value"]
                                   for trace in (model_trace, guide_trace)
                                   for site in trace.nodes.values()
                                   if site["type"] == "param")

            if trainable_params and getattr(surrogate_elbo_particle, 'requires_grad', False):
                surrogate_loss_particle = -surrogate_elbo_particle / self.num_particles
                surrogate_loss_particle.backward()
                pyro.get_param_store().mark_params_active(trainable_params)

        loss = -elbo
        if is_nan(loss):
            warnings.warn('Encountered NAN loss')
        return loss
