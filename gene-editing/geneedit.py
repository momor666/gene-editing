# The objective of this simulation is to consider several scenarios for the management of multiple
# recessive alleles in a simulated population of dairy cattle. The basic idea is simple:
# + Each animal has parents, a sex code, a true breeding value for lifetime net merit, and a genotype
#   for the recessive alleles in the population;
# + Each recessive has a minor allele frequency in the base population and an economic value;
# + Matings will be based on parent averages, and at-risk matings will be penalized by the economic
#   value of each recessive.

# Authors:
#
# John B. Cole (john.cole@ars.usda.gov), Animal Genomics and Improvement Laboratory, ARS, USDA
# Maci L. Mueller (macmueller@ucdavis.edu), Department of Animal Science, University of California, Davis

# Force matplotlib to not use any X-windows backend.
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.pyplot import hist
import cerberus
import collections
import copy
import datetime
import itertools
import math
import numpy as np
import numpy.ma as ma
import os
import pandas as pd
import random
from scipy.stats import bernoulli
import subprocess
import sys
import time
import types


###
# TODO
###
# + Add some way to force relationships among polled bulls to resemble those in the real world using
#   some of the information from Scheper et al. (2016; GSE 48:50,
#   https://gsejournal.biomedcentral.com/articles/10.1186/s12711-016-0228-7). Most notably, polled bulls
#   are about twice as inbred and twice as related as polled cows.
#
# + Convert animal records to dictionaries
#
# + Move the simulation settings and allele frequencies writers into separate functions outside of
#   run_scenario().
##

# Setup the simulation and create the base population.
#
# 02/26/2019    JBC    Added 'base_max_bull_age', 'base_max_cow_age'.


def create_base_population(cow_mean, genetic_sd=200., bull_diff=1.5, polled_diff=[1.0,1.3], base_bulls=500,
                           base_max_bull_age=10, base_cows=2500, base_max_cow_age=5, base_herds=100,
                           force_carriers=True, force_best=True, recessives={}, check_tbv=False, rng_seed=None,
                           base_polled='homo', polled_parms=[], use_nucleus=False,
                           debug=True, extras=()):

    """Setup the simulation and create the base population.

    :param cow_mean: Average base population cow TBV.
    :type cow_mean: float
    :param genetic_sd: Additive genetic SD of the simulated trait.
    :type genetic_sd: float
    :param bull_diff: Differential between base cows and bulls, in genetic SD.
    :type bull_diff: float
    :parm polled_diff: Difference between Pp and pp bulls, and PP and pp bulls, in genetic SD.
    :type polled_diff: List of floats
    :param base_bulls: (optional)Number of bulls in the base population (founders)
    :type base_bulls: int
    :param base_max_bull_age: Maximum age of base population bulls
    :type base_max_bull_age: int
    :param base_cows: (optional) Number of cows in the base population (founders)
    :type base_cows: int
    :param base_max_cow_age: Maximum age of base population cows
    :type base_max_cow_age: int
    :param base_herds: (optional) Number of herds in the population.
    :type base_herds: int
    :param force_carriers: (optional) Boolean. Force at least one carrier of each sex.
    :type force_carriers: bool
    :param force_best: (optional) Boolean. Force one carrier of each breed to have a TBV 4 SD above the mean.
    :type force_best: bool
    :param recessives: Dictionary of recessive alleles in the population.
    :type recessives: dictionary
    :param check_tbv: (optional) Boolean. Plot histograms of sire and dam TBV in the base population.
    :type check_tbv: bool
    :param rng_seed: (optional) Seed used for the random number generator.
    :type rng_seed: int
    :param base_polled: Genotype of polled animals in the base population ('homo'|'het'|'both')
    :type base_polled: string
    :param polled_parms: List. Proportion of polled bulls, proportion of PP, and proportion of Pp bulls.
    :type polled_parms: list of floats
    :param use_nucleus: Create and use nucleus herds to propagate elite genetics
    :type use_nucleus: bool
    :param debug: (optional) Boolean. Activate debugging messages.
    :type debug: bool
    :param extras: List(s) of "extra" animals to be used for assigning IDs when nucleus herds are used.
    :type extras: tuple
    :return: Separate lists of cows, bulls, dead cows, dead bulls, and the histogram of TBV.
    :rtype: list
    """

    #print len(extras)

    # Base population parameters
    generation = 0                  # The simulation starts at generation 0. It's as though we're all C programmers.

    # Recessives are required since that's the whole point of this.
    if len(recessives) == 0:
        print '[create_base_population]: The recessives dictionary passed to the setup() subroutine was empty! The program \
            cannot continue, and will halt.'
        sys.exit(1)

    # Seed the RNG
    if rng_seed:
        np.random.seed(rng_seed)
    else:
        np.random.seed()

    # The mean and standard deviation of the trait used to rank animals and make mating
    # decisions. The values here are for lifetime net merit in US dollars.
    #mu_cows = 0.
    mu_cows = cow_mean
    #sigma = 200.
    sigma = genetic_sd

    # Create the base population of cows and bulls.

    # Assume bulls average 1.5 SD better than the cows.
    #mu_bulls = mu_cows + (sigma * 1.5)
    mu_bulls = mu_cows + (sigma * bull_diff)

    # Make true breeding values
    base_cow_tbv = (sigma * np.random.randn(base_cows, 1)) + mu_cows
    base_bull_tbv = (sigma * np.random.randn(base_bulls, 1)) + mu_bulls

    # This dictionary will be used to store allele frequencies for each generation
    # of the simulation.
    freq_hist = {}
    freq_hist[0] = []
    for rk, rv in recessives.iteritems():
        freq_hist[0].append(rv['frequency'])
     
    # Make the recessives. This is a little tricky. We know the minor
    # allele frequency in the base population, but we're going to simulate
    # the base population genotypes. We'll then calculate the allele
    # frequency in the next generation, which means that we need a table to
    # store the values over time. This is based on formulas presented in:
    #
    #     Van Doormaal, B.J., and G.J. Kistemaker. 2008. Managing genetic
    #     recessives in Canadian Holsteins. Interbull Bull. 38:70-74.
    #
    # On 6/2/14 I changed this so that there can be non-lethal recessives
    # (e.g., polled) so that I can convince myself that the code is working
    # okay.

    # Initialize an array of zeros that will be filled with genotypes
    base_cow_gt = np.zeros((base_cows, len(recessives)))
    base_bull_gt = np.zeros((base_bulls, len(recessives)))
    for rk, rv in recessives.iteritems():
        # Get the location of the recessive in the dictionary
        r = recessives.keys().index(rk)
        # Get the MAF for the current recessive
        r_freq = rv['frequency']
        # The recessive is a lethal
        if rv['lethal'] == 1:
            # Compute the frequency of the AA and Aa genotypes
            denom = (1. - r_freq)**2 + (2 * r_freq * (1. - r_freq))
            f_dom = (1. - r_freq)**2 / denom
            f_het = (2 * r_freq * (1. - r_freq)) / denom
            print 'This recessive is ***LETHAL***'
            print 'Recessive %s (%s), generation %s:' % (r, rk, generation)
            print '\tp = %s' % (1. - r_freq)
            print '\tq = %s' % r_freq
            print '\tf(AA) = %s' % f_dom
            print '\tf(Aa) = %s' % f_het
            # Assign genotypes by drawing a random Bernoulli variate where the
            # parameter is the probability of an AA genotype. A value of 1 means
            # "AA", and a value of 0 means "Aa".
            for c in xrange(base_cows):
                base_cow_gt[c, r] = int(bernoulli.rvs(f_dom))
            for b in xrange(base_bulls):
                base_bull_gt[b, r] = int(bernoulli.rvs(f_dom))
            if force_carriers:
                # I want to force at least one carrier for each mutation so that the
                # vagaries of the RNG don't thwart me.
                base_cow_gt[r, r] = 0
                base_bull_gt[r, r] = 0
                print '\t[create_base_population]: Forcing carriers to bend Nature to my will...'
                print '\t[create_base_population]: \tCow %s is a carrier for recessive %s (%s)' % (r, rk, r)
                print '\t[create_base_population]: \tBull %s is a carrier for recessive %s (%s)' % (r, rk, r)
        # The recessive is NOT lethal
        else:
            # Compute the frequency of the AA and Aa genotypes
            f_dom = (1. - r_freq)**2
            f_het = (2 * r_freq * (1. - r_freq))
            f_rec = r_freq**2
            print 'This recessive is ***NOT LETHAL***'
            print 'Recessive %s (%s), generation %s:' % (r, rk, generation)
            print '\tp = %s' % (1. - r_freq)
            print '\tq = %s' % r_freq
            print '\tf(AA) = %s' % f_dom
            print '\tf(Aa) = %s' % f_het
            print '\tf(aa) = %s' % f_rec
            # Assign genotypes by drawing a random Bernoulli variate for each
            # parental allele. The parameter is the probability of an "A" allele.
            # A value of 1 assigned to the cow (bull) genotype means "AA", a
            # value of 0 means "Aa", and a value of -1 means "aa".
            for c in xrange(base_cows):
                # Get the cow's genotype -- since the parameter we're
                # using is the major allele frequency (p), a success (1) is
                # an "A" allele, and a failure is an "a" allele.
                s_allele = bernoulli.rvs(1. - r_freq)
                d_allele = bernoulli.rvs(1. - r_freq)
                if s_allele == 1 and d_allele == 1:
                    base_cow_gt[c, r] = 1
                elif s_allele == 0 and d_allele == 0:
                    base_cow_gt[c, r] = -1
                else:
                    base_cow_gt[c, r] = 0
                # If the user has requested specific horned genotypes assign them
                if rk == 'Horned' and base_polled == 'homo':
                    if base_cow_gt[c, r] == 0:
                        base_cow_gt[c, r] = 1
                elif rk == 'Horned' and base_polled == 'het':
                    if base_cow_gt[c, r] == 1:
                        base_cow_gt[c, r] = 0
                elif rk == 'Horned' and base_polled == 'both':
                    # Don't change heterozygotes to homozygotes, or vice versa
                    pass
                else:
                    pass
            for b in xrange(base_bulls):
                # Get the bull's genotype -- since the parameter we're
                # using is the major allele frequency (p), a success (1) is
                # an "A" allele, and a failure is an "a" allele.
                s_allele = bernoulli.rvs(1. - r_freq)
                d_allele = bernoulli.rvs(1. - r_freq)
                if s_allele == 1 and d_allele == 1:
                    base_bull_gt[b, r] = 1
                elif s_allele == 0 and d_allele == 0:
                    base_bull_gt[b, r] = -1
                else:
                    base_bull_gt[b, r] = 0
                # If the user has requested specific horned genotypes assign them
                if rk == 'Horned' and base_polled == 'homo':
                    if base_bull_gt[b, r] == 0:
                        base_bull_gt[b, r] = 1
                elif rk == 'Horned' and base_polled == 'het':
                    if base_bull_gt[b, r] == 1:
                        base_bull_gt[b, r] = 0
                elif rk == 'Horned' and base_polled == 'both':
                    # Don't change heterozygotes to homozygotes, or vice versa
                    pass
                else:
                    pass

            # You may want to force at least one carrier for each mutation so that the
            # vagaries of the RNG don't thwart you. If you don't do this, then your
            # base population may not have any minor alleles for a rare recessive.
            if force_carriers:
                if rk == 'Horned' and base_polled == 'homo':
                    base_cow_gt[r, r] = 1
                    base_bull_gt[r, r] = 1
                elif rk == 'Horned' and base_polled == 'homo':
                    base_cow_gt[r, r] = 0
                    base_bull_gt[r, r] = 0
                elif rk == 'Horned' and base_polled == 'both':
                    if random.uniform(0,1) <0.66:
                        base_cow_gt[r, r] = 0
                    else:
                        base_cow_gt[r, r] = 1
                    if random.uniform(0,1) <0.66:
                        base_bull_gt[r, r] = 0
                    else:
                        base_bull_gt[r, r] = 1
                else:
                    pass
                print '\t[create_base_population]: Forcing there to be a carrier for each recessive, i.e., bending Nature to my will.'
                print '\t[create_base_population]: \tCow %s is a carrier for recessive %s (%s)' % (r, r, rk)
                print '\t[create_base_population]: \tBull %s is a carrier for recessive %s (%s)' % (r, r, rk)

    # Storage
    cows = []                       # List of live cows in the population
    bulls = []                      # List of live bulls in the population
    dead_cows = []                  # List of dead cows in the population (history)
    dead_bulls = []                 # List of dead bulls in the population (history)
    id_list = []

    # Some polled bulls were created by chance in the "make bulls" loop above. We can increase the frequency
    # of polled bulls to match a model population, such as the polled bulls available in 2013 as in Spurlock's
    # paper. This means that, technically, the frequency of polled bulls will be what's in polled_parms plus
    # 1-P(Horned).
    if polled_parms != [] and polled_parms[0] > 0.:
        for b in xrange(base_bulls):
            # Should this bull be polled?
            if bernoulli.rvs(polled_parms[0]):
                # Should this bull be PP?
                if bernoulli.rvs(polled_parms[1]):
                    base_bull_gt[b, r] = 1
                # If not, then it's Pp.
                else:
                    base_bull_gt[b, r] = 0
    # Don't match a population and proceed.
    else:
        pass


    # Assume that polled bulls average ~1 SD lower genetic merit than horned bulls, based on average
    # PTA for NM$ of $590 versus $761 (difference of $171) from Spurlock et al., 2014,
    # http://dx.doi.org/10.3168/jds.2013-7746.
    if 'Horned' in recessives.keys():
        horned = True
        horned_loc = recessives.keys().index('Horned')
    else:
        horned = False
        horned_loc = -1

    # Add animals to the base cow list.
    if debug:
        print '\t[create_base_population]: Adding animals to the base cow list at %s' % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    # If the animals being generated are for a nucleus herd, then make sure the IDs assigned start at the
    # end of the range for existing non-nucleus animals to avoid overlapping IDs that cause all sorts of
    # problems downstream.
    if use_nucleus:
        id_offset = get_next_id(cows, bulls, dead_cows, dead_bulls, *extras)
    else:
        id_offset = 0

    for i in xrange(base_cows):
        # The list contents are:
        # animal ID, sire ID, dam ID, generation, sex, herd, alive/dead, reason dead, when dead, TBV,
        # # coefficient of inbreeding, editing status, and genotype
        # "generation" is the generation in which the base population animal was born, not its actual
        # age.
        c = i + 1 + id_offset
        if c in id_list:
            if debug:
                print '\t[create_base_population]: Error! A cow with ID %s already exists in the ID list!' % c
        # base_cow_max_age may differ between, e.g., beef and dairy scenarios.
        #c_list = [c, 0, 0, (-1*random.randint(0, base_max_cow_age-1)), 'F', random.randint(0, base_herds-1), 'A',
        #          '', -1, base_cow_tbv.item(i), 0.0, [], [], [], 0, []]
        c_dict = {'animal': c, 'sire': 0, 'dam': 0, 'born': (-1*random.randint(0, base_max_cow_age-1)),
                  'sex': 'F', 'herd': random.randint(0, base_herds-1), 'alive': 'A', 'reason': '',
                  'died': -1, 'tbv': base_cow_tbv.item(i), 'inbreeding': 0.0, 'edit_status': [],
                  'edit_count': [], 'et_count': [], 'bred': 0, 'genotypes': []}
        for rk in recessives.keys():
            c_dict['genotypes'].append(int(base_cow_gt.item(i, recessives.keys().index(rk))))
            c_dict['edit_status'].append(0)    # Edit status
            c_dict['edit_count'].append(0)     # Edit count
        cows.append(c_dict)
        id_list.append(c)
    # Add animals to the bull list.
    if debug:
        print '\t[create_base_population]: Adding animals to the base bull list at %s' % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    for i in xrange(base_bulls):
        b = i + 1 + base_cows + id_offset
        if b in id_list:
            if debug:
                print '\t[create_base_population]: Error! A bull with ID %s already exists in the ID list!' % b
        if horned and base_bull_gt[i][horned_loc] == 0:                     # 0 = Pp
            bull_tbv = base_bull_tbv.item(i) - (sigma * polled_diff[1])
        elif horned and base_bull_gt[i][horned_loc] == 1:                   # 1 = PP
            bull_tbv = base_bull_tbv.item(i) - (sigma * polled_diff[0])
        else:
            bull_tbv = base_bull_tbv.item(i)
        # base_bull_max_age may differ between, e.g., beef and dairy scenarios.
        #b_list = [b, 0, 0, (-1 * random.randint(0, base_max_bull_age-1)), 'M', random.randint(0, base_herds - 1), 'A', '',
        #          -1, bull_tbv, 0.0, [], [], [], 0, []]
        b_dict = {'animal': b, 'sire': 0, 'dam': 0, 'born': (-1*random.randint(0, base_max_bull_age-1)),
                  'sex': 'F', 'herd': random.randint(0, base_herds-1), 'alive': 'A', 'reason': '',
                  'died': -1, 'tbv': bull_tbv, 'inbreeding': 0.0, 'edit_status': [],
                  'edit_count': [], 'et_count': [], 'bred': 0, 'genotypes': []}
        for rk in recessives.keys():
            b_dict['genotypes'].append(int(base_bull_gt.item(i, recessives.keys().index(rk))))
            b_dict['edit_status'].append(0)    # Edit status
            b_dict['edit_count'].append(0)     # Edit count
        bulls.append(b_dict)
        id_list.append(b)

    ### This worked fine in an IPython notebook, needs check here.
    if check_tbv:
        # Check the distribution of bull and cow TBV
        #min_data = np.r_[base_cow_tbv, base_bull_tbv].min()
        #max_data = np.r_[base_cow_tbv, base_bull_tbv].max()
        #print min_data, max_data
        hist(base_cow_tbv, normed=True, color="#6495ED", alpha=.5)
        hist(base_bull_tbv, normed=True, color="#F08080", alpha=.5)

    return cows, bulls, dead_cows, dead_bulls, freq_hist


# Okay, now we've got at least a rough draft of the setup. Now we need to get code in place
# to simulate generation 1, which can then be generalized to *n* generations. In order to do
# this, we actually need to make a bunch of decisions. Here's an outline of what needs to happen
# each generation:
# 
# * The generation counter needs to be incremented
# * We need to mate cows and create their offspring, including genotypes
# * "Old" cows need to be culled so that the population size is maintained
# * Minor allele frequencies in the recessives lists need to be updated
#
# JBC    02/26/2019    Added MM's 'harvest_beef' parm, updated docstring.


def random_mating(cows, bulls, dead_cows, dead_bulls, generation, generations, recessives,
                  max_matings=50, edit_prop=[0.0, 0.0], edit_type='C', edit_trials=1,
                  embryo_trials=1, edit_sex='M', calf_loss=0.0, dehorning_loss=0.0,
                  harvest_beef=0.0, debug=False, extras=()):

    """Use random mating to advance the simulation by one generation.

    :param cows: A list of live cow records.
    :type cows: list
    :param bulls: A list of live bull records.
    :type bulls: list
    :param dead_cows: A list of dead cow records.
    :type dead_cows: list
    :param dead_bulls: A list of dead bull records.
    :type dead_bulls: list
    :param generation: The current generation in the simulation.
    :type generation: int
    :param generations: The total number of generations in the simulation.
    :type generations: int
    :param recessives: A dictionary of recessives in the population.
    :type recessives: dictionary
    :param max_matings: The maximum number of matings permitted for each bull.
    :type max_matings: int
    :param edit_prop: The proportion of animals to edit based on TBV (e.g., 0.01 = 1 %).
    :type edit_prop: list
    :param edit_type: Tool used to edit genes: 'Z' = ZFN, 'T' = TALEN, 'C' = CRISPR, 'P' = no errors.
    :type edit_type: char
    :param edit_trials: The number of attempts to edit an embryo successfully (-1 = repeat until success).
    :type edit_trials: int
    :param embryo_trials: The number of attempts to transfer an edited embryo successfully (-1 = repeat until success).
    :type embryo_trials: int
    :param edit_sex: The sex of animals to be edited (M = males, F = females).
    :type edit_sex: char
    :param calf_loss: Proportion of calves that die before they reach 1 year of age.
    :type calf_loss: float
    :param dehorning_loss: Proportion of calves that die bfrom dehorning complications.
    :type dehorning_loss: float
    :param harvest_beef: Proportion of calves culled for beef?
    :type harvest_beef: float
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :param extras: List(s) of "extra" animals to be used for assigning IDs when nucleus herds are used.
    :type extras: tuple
    :return: Separate lists of cows, bulls, dead cows, and dead bulls.
    :rtype: list
    """

    if max_matings <= 0:
        print "[random_mating]: max_matings cannot be <= 0! Setting to 50."
        max_matings = 50
    if max_matings * len(bulls) < len(cows):
        print "[random_mating]: You don't have enough matings to breed all cows1"

    # Make a list of bulls so that we can track the number of matings for each
    matings = {}
    new_cows = []
    new_bulls = []
    for b in bulls:
        matings[b['animal']] = 0

    # Get the ID for the next calf
    #next_id = len(cows) + len(bulls) + len(dead_cows) + len(dead_bulls) + 1
    next_id = get_next_id(cows, bulls, dead_cows, dead_bulls, *args)
    # Now we need to randomly assign mates. We do this as follows:
    #     1. Loop over cow list
    if debug:
        print '%s bulls in list for mating' % len(bulls)
        print '%s cows in list for mating' % len(cows)
    for c in cows:
        # Is the cow alive?
        if c['alive'] == 'A':
            cow_id = c['animal']
            mated = False
            if debug:
                print 'Mating cow %s' % cow_id
            while not mated:
                # 2. For each cow, pick a bull at random
                bull_to_use = random.randint(0, len(bulls)-1)
                bull_id = bulls[bull_to_use]['animal']
                if debug:
                    print 'Using bull %s (ID %s)' % (bull_to_use, bull_id)
                    # 3. If the bull is alive and has matings left then use him
                if bulls[bull_to_use]['alive'] == 'A' and matings[bull_id] < max_matings:
                    if debug:
                        print 'bull %s (ID %s) is alive and has available matings' % (bull_to_use, bull_id)
                    # Create the resulting calf
                    calf = create_new_calf(bulls[bull_to_use], c, recessives, next_id, generation, calf_loss,
                                           dehorning_loss, harvest_beef, debug=debug)
                    if debug:
                        print calf
                    if calf['sex'] == 'F':
                        new_cows.append(calf)
                    else:
                        new_bulls.append(calf)
                        # Done!
                    next_id += 1
                    mated = True
                else:
                    if debug:
                        print 'bull %s (ID %s) is not alive or does not have available matings' % (bull_to_use, bull_id)
                cow['bred'] = 0
            # The cow has been bred
            cow[14] = 1

    for nc in new_cows:
        if nc['alive'] == 'A':
            cows.append(nc)
        else:
            dead_cows.append(nc)
    for nb in new_bulls:
        if nb['alive'] == 'A':
            bulls.append(nb)
        else:
            dead_bulls.append(nb)

    # If gene editing is going to happen, it happens here
    do_edits = [rv['edit'] for rv in recessives.values()]
    if '1' in do_edits:
        if edit_prop[0] > 0.0:
            cows, bulls, dead_cows, dead_bulls = edit_genes(cows, bulls, dead_cows, dead_bulls,
                                                            recessives, generation, edit_prop[0],
                                                            edit_type, edit_trials, embryo_trials,
                                                            edit_sex='M', debug=debug, extras=extras)
        if edit_prop[1] > 0.0:
            cows, bulls, dead_cows, dead_bulls = edit_genes(cows, bulls, dead_cows, dead_bulls,
                                                            recessives, generation, edit_prop[1],
                                                            edit_type, edit_trials, embryo_trials,
                                                            edit_sex='F', debug=debug, extras=extras)
    # End of gene editing section

    # Make sure we have current coefficients of inbreeding -- we'll need them for matings in the next generation.
    filetag = 'random'
    cows, bulls, dead_cows, dead_bulls, matings, bull_portfolio, cow_portfolio, inbr = compute_inbreeding(cows, bulls, dead_cows,
                                                                                           dead_bulls, generation,
                                                                                           generations, filetag, debug)

    return cows, bulls, dead_cows, dead_bulls


# Okay, now we've got at least a rough draft of the setup. Now we need to get
# code in place to simulate generation 1, which can then be generalized to *n*
# generations. In order to do this, we actually need to make a bunch of
# decisions. Here's an outline of what needs to happen each generation:
# 
# * The generation counter needs to be incremented
# * We need to make a list of the top "pct" bulls, and there is no limit
#   to the number of matings for each bull, so we will mate randomly from
#   within the top group.
# * We need to mate cows and create their offspring, including genotypes
# * "Old" cows need to be culled so that the population size is maintained
# * Minor allele frequencies in the recessives lists need to be updated
#
# JBC    02/26/2019    Added MM's 'harvest_beef' parm, updated docstring.


def truncation_mating(cows, bulls, dead_cows, dead_bulls, generation, generations,
                  recessives, pct=0.10, edit_prop=[0.0,0.0], edit_type='C',
                  edit_trials=1, embryo_trials=1, edit_sex='M', calf_loss=0.0,
                  dehorning_loss=0.0, harvest_beef=0.0, debug=False, extras=()):

    """Use truncation selection to advance the simulation by one generation.

    :param cows: A list of live cow records.
    :type cows: list
    :param bulls: A list of live bull records.
    :type bulls: list
    :param dead_cows: A list of dead cow records.
    :type dead_cows: list
    :param dead_bulls: A list of dead bull records.
    :type dead_bulls: list
    :param generation: The current generation in the simulation.
    :type generation: int
    :param generations: The total number of generations in the simulation.
    :type generations: int
    :param recessives: A dictionary of recessives in the population.
    :type recessives: dictionary
    :param pct: The proportion of bulls to retain for mating.
    :type pct: float
    :param edit_prop: The proportion of animals to edit based on TBV (e.g., 0.01 = 1 %).
    :type edit_prop: list
    :param edit_type: Tool used to edit genes: 'Z' = ZFN, 'T' = TALEN, 'C' = CRISPR, 'P' = no errors.
    :type edit_type: char
    :param edit_trials: The number of attempts to edit an embryo successfully (-1 = repeat until success).
    :type edit_trials: int
    :param embryo_trials: The number of attempts to transfer an edited embryo successfully (-1 = repeat until success).
    :type embryo_trials: int
    :param edit_sex: The sex of animals to be edited (M = males, F = females).
    :type edit_sex: char
    :param calf_loss: Proportion of calves that die before they reach 1 year of age.
    :type calf_loss: float
    :param dehorning_loss: The proportion of cows that die during dehorning.
    :type dehorning_loss: float
    :param harvest_beef: Proportion of calves culled for beef?
    :type harvest_beef: float
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :param extras: List(s) of "extra" animals to be used for assigning IDs when nucleus herds are used.
    :type extras: tuple
    :return: Separate lists of cows, bulls, dead cows, and dead bulls.
    :rtype: list
    """

    if debug:
        print '[truncation_mating]: PARMS:\n\tgeneration: %s\n\trecessives; %s\n\tpct: %s\n\tdebug: %s' % \
            (generation, recessives, pct, debug)
    # Never trust users, they are lying liars
    if pct < 0.0 or pct > 1.0:
        print '[truncation_mating]: %s is outside of the range 0.0 <= pct <= 1.0, changing to 0.10' % pct
        pct = 0.10

    # Sort bulls on TBV in ascending order
    bulls.sort(key=lambda x: x['tbv'])
    # How many do we keep?
    b2k = int(pct*len(bulls))
    if debug:
        print '[truncation_mating]: Using %s bulls for mating' % b2k
    # Set-up data structures
    new_cows = []
    new_bulls = []
    #next_id = len(cows) + len(bulls) + len(dead_cows) + len(dead_bulls) + 1
    next_id = get_next_id(cows, bulls, dead_cows, dead_bulls, *extras)
    # Now we need to randomly assign mates. We do this as follows:
    #     1. Loop over cow list
    if debug: 
        print '\t[truncation_mating]: %s bulls in list for mating' % len(bulls)
        print '\t[truncation_mating]: %s cows in list for mating' % len(cows)
    for c in cows:
        # Is the cow alive?
        if c['alive'] == 'A':
            cow_id = c['animal']
            mated = False
            if debug:
                print '\t[truncation_mating]: Mating cow %s' % cow_id
            while not mated:
                # 2. For each cow, pick a bull at random
                # Note the offset index to account for the fact that we're picking only
                # from the top pct of the bulls.
                bull_to_use = random.randint(len(bulls)-b2k, len(bulls)-1)
                bull_id = bulls[bull_to_use]['animal']
                #if debug: print 'Using bull %s (ID %s)' % ( bull_to_use, bull_id )
                # 3. If the bull is alive then use him
                if bulls[bull_to_use]['alive'] == 'A':
                    if debug:
                        print 'bull %s (ID %s) is alive' % (bull_to_use, bull_id)
                    # Create the resulting calf
                    calf = create_new_calf(bulls[bull_to_use], c, recessives, next_id, generation, calf_loss,
                                           dehorning_loss, harvest_beef, debug=debug)
                    if debug:
                        print calf
                    if calf['sex'] == 'F':
                        new_cows.append(calf)
                    else:
                        new_bulls.append(calf)
                        # Done!
                    next_id += 1
                    mated = True
                else:
                    if debug:
                        print '[truncation_mating]: bull %s (ID %s) is not alive' % (bull_to_use, bull_id)
                cow['bred'] = 0
            # The cow has been bred
            cow['bred'] = 1

    if debug:
        print '\t[truncation_mating]: %s animals in original cow list' % len(cows)
        print '\t[truncation_mating]: %s animals in new cow list' % len(new_cows)
        print '\t[truncation_mating]: %s animals in original bull list' % len(bulls)
        print '\t[truncation_mating]: %s animals in new bull list' % len(new_bulls)


    for nc in new_cows:
        if nc['alive'] == 'A':
            cows.append(nc)
        else:
            dead_cows.append(nc)
    for nb in new_bulls:
        if nb['alive'] == 'A':
            bulls.append(nb)
        else:
            dead_bulls.append(nb)

    # If gene editing is going to happen, it happens here
    do_edits = [rv['edit'] for rv in recessives.values()]
    if '1' in do_edits:
        if edit_prop[0] > 0.0:
            cows, bulls, dead_cows, dead_bulls = edit_genes(cows, bulls, dead_cows, dead_bulls,
                                                            recessives, generation, edit_prop[0],
                                                            edit_type, edit_trials, embryo_trials,
                                                            edit_sex='M', debug=debug, extras=extras)
        if edit_prop[1] > 0.0:
            cows, bulls, dead_cows, dead_bulls = edit_genes(cows, bulls, dead_cows, dead_bulls,
                                                            recessives, generation, edit_prop[1],
                                                            edit_type, edit_trials, embryo_trials,
                                                            edit_sex='F', debug=debug, extras=extras)
    # End of gene editing section

    if debug:
        print '\t[truncation_mating]: %s animals in final cow list' % len(cows)
        print '\t[truncation_mating]: %s animals in final bull list' % len(bulls)

    # Make sure we have current coefficients of inbreeding -- we'll need them for matings in the next generation.
    filetag = 'truncation'
    cows, bulls, dead_cows, dead_bulls, matings, bull_portfolio, cow_portfolio, inbr = compute_inbreeding(cows, bulls, dead_cows,
                                                                                           dead_bulls, generation,
                                                                                           generations, filetag, debug)

    return cows, bulls, dead_cows, dead_bulls


# This function returns the largest animal ID in the population + 1, which
# is used as the starting ID for the next generation of calves.


def get_next_id(cows, bulls, dead_cows, dead_bulls, *args):
    """Returns the largest animal ID in the population + 1.

    :param cows: A list of live cow records.
    :type cows: list
    :param bulls: A list of live bull records.
    :type bulls: list
    :param dead_cows: A list of dead cow records.
    :type dead_cows: list
    :param dead_bulls: A list of dead bull records.
    :type dead_bulls: list
    :return: The starting ID for the next generation of calves.
    :rtype: int
    """

    #print 'len(args): ', len(args)

    id_list = []
    for c in cows:
        id_list.append(int(c['animal']))
    for dc in dead_cows:
        id_list.append(int(dc['animal']))
    for b in bulls:
        id_list.append(int(b['animal']))
    for db in dead_bulls:
        id_list.append(int(db['animal']))
    for other in args:
        for o in other:
            id_list.append(int(o['animal']))
    id_list.sort()
    #print id_list[-10:]
    next_id = id_list[-1] + 1
    return next_id


def compute_inbreeding(cows, bulls, dead_cows, dead_bulls, generation, generations, filetag='',
                       penalty=False, proxy_matings=False, bull_criterion='random', bull_deficit='use_horned',
                       bull_copies=4, bull_unique=False, base_herds=100, service_bulls=50, debug=False):

    """Compute coefficients of inbreeding for each animal in the pedigree.

    :param cows: A list of live cow records.
    :type cows: list
    :param bulls: A list of live bull records.
    :type bulls: list
    :param dead_cows: A list of dead cow records.
    :type dead_cows: list
    :param dead_bulls: A list of dead bull records.
    :type dead_bulls: list
    :param generation: The current generation in the simulation.
    :type generation: int
    :param generations: The total number of generations in the simulation.
    :type generations: int
    :param filetag: Prefix used for filenames to tell scenarios apart.
    :type filetag: string
    :param penalty: Boolean. Adjust PA for recessives, or adjust only for inbreeding
    :type penalty: bool
    :param proxy_matings: Form "dummy" calves to get inbreeding of matings of all cows to all bulls..
    :type proxy_matings: bool
    :param bull_criterion: Criterion used to select the group of bulls for mating.
    :type bull_criterion: string
    :param bull_deficit: Manner of handling too few bulls for matings: 'use_horned' or 'no_limit'.
    :type bull_deficit: string
    :param bull_copies: Genotype of polled bulls selected for mating (0|1|2|4|5|6)
    :type bull_copies: integer
    :param bull_unique: Each bull portfolio should be unique.
    :type bull_unique: boolean
    :param base_herds: Number of herds in the population.
    :type base_herds: int
    :param service_bulls: Number of herd bulls to use in each herd each generation.
    :type service_bulls: int
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :return: Separate lists of cows, bulls, dead cows, and dead bulls with updated inbreeding.
    :rtype: list
    """

    if debug:
            print '\t\t[compute_inbreeding]: generation     : %s' % generation
            print '\t\t[compute_inbreeding]: generations    : %s' % generations
            print '\t\t[compute_inbreeding]: filetag        : %s' % filetag
            print '\t\t[compute_inbreeding]: penalty        : %s' % penalty
            print '\t\t[compute_inbreeding]: proxy_matings  : %s' % proxy_matings
            print '\t\t[compute_inbreeding]: bull_criterion : %s' % bull_criterion
            print '\t\t[compute_inbreeding]: bull_deficit   : %s' % bull_deficit
            print '\t\t[compute_inbreeding]: bull_unique    : %s' % bull_unique
            print '\t\t[compute_inbreeding]: debug          : %s' % debug

    # Now, we're going to need to construct a pedigree that includes matings of all cows in
    # each herd to the bulls randomly assigned to that herd. Bulls are randomly assigned to
    # herds to reflect different sire selection policies. It is faster to calculate the
    # inbreeding of the potential offspring than it is to calculate relationships among parents
    # because the latter requires that we store relationships among all parents.
    #
    # I'm going to try and allocate a fixed-length NumPy array large enough to store the entire
    # pedigree to avoid memory fragmentation/swapping when working with large Python lists. If that
    # can't be done, I'll fall back to a list. This does mean that the code has to accommodate both
    # cases, so it's a little verbose.
    #next_id = get_next_id(cows, bulls, dead_cows, dead_bulls)
    #if debug:
    #    print '\t[compute_inbreeding]: next_id = %s in generation %s' % (next_id, generation)
    pedigree_size = len(cows) + len(dead_cows) + len(bulls) + len(dead_bulls)
    # If we're doing proxy matings then we need a few more variables.
    if proxy_matings:
        # Note that I'm including a fudge factor by multiplying the bulls by 2 so that we get an
        # array longer than we need.
        pedigree_size += (2 * len(cows) * service_bulls)    # Leave room for new calves
        matings = {}
        bull_portfolio = {}
        cow_portfolio = {}
    # Can we allocate enough memory for this pedigree, or do we need to get
    # Gene Kranz on the horn?
    try:
        pedigree = np.zeros((pedigree_size,), dtype=('a20, a20, a20, i4'))
        print '\t[compute_inbreeding]: Allocated a NumPy array of size %s to store pedigree at %s' % \
              (pedigree_size, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    except MemoryError:
        pedigree = []
        print '\t[compute_inbreeding]: Could not allocate an array of size %s, using a SLOWWWW Python list at %s' % \
              (pedigree_size, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    id_list = []
    pedigree_counter = 0
    pedigree_array = isinstance(pedigree, (np.ndarray, np.generic))
    if debug:
        print '\t[compute_inbreeding]: Putting all cows and bulls in a pedigree at %s' % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    for c in cows:
        if pedigree_array:
            pedigree[pedigree_counter] = (c['animal'], c['sire'], c['dam'], c['born']+10)
        else:
            pedigree.append(' '.join([c['animal'], c['sire'], c['dam'], c['born']+10, '\n']))
            if c['animal'] not in id_list:
                id_list.append(c['animal'])
        pedigree_counter += 1
    for dc in dead_cows:
        if pedigree_array:
            pedigree[pedigree_counter] = (dc['animal'], dc['sire'], dc['dam'], dc['born']+10)
        else:
            pedigree.append(' '.join([dc['animal'], dc['sire'], dc['dam'], dc['born']+10, '\n']))
            if dc['animal'] not in id_list:
                id_list.append(dc['animal'])
        pedigree_counter += 1
    for b in bulls:
        if pedigree_array:
            pedigree[pedigree_counter] = (b['animal'], b['sire'], b['dam'], b['born']+10)
        else:
            pedigree.append(' '.join([b['animal'], b['sire'], b['dam'], b['born']+10, '\n']))
            if b['animal'] not in id_list:
                id_list.append(b['animal'])
        pedigree_counter += 1
        matings[b['animal']] = 0
    for db in dead_bulls:
        if isinstance(pedigree, (np.ndarray, np.generic)):
            pedigree[pedigree_counter] = (db['animal'], db['sire'], db['dam'], db['born']+10)
        else:
            pedigree.append(' '.join([db['animal'], db['sire'], db['dam'], db['born']+10, '\n']))
            if db['animal'] not in id_list:
                id_list.append(db['animal'])
        pedigree_counter += 1
    if debug:
        print '\t[compute_inbreeding]: %s "old" animals in pedigree in generation %s at %s' % \
            (pedigree_counter, generation, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    #
    #
    # PROXY MATING CODE
    #
    #
    if proxy_matings:
        # We need to fake offspring of living bulls and cows because it's faster to compute inbreeding than relationships.
        calfcount = 0
        print '\t[fetch_ages]: Now we are fetching cows that are of mating age'
        aged_cows = fetch_ages(cows, generation, bull_mating_age, cow_mating_age, messages=True, debug=debug)
        if debug:
            print '\t[compute_inbreeding]: Mating all cows to all herd bulls at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        print '\t[fetch_ages]:Now we are fetching bulls'
        polled_sire_count_message = True
        fetch_rec_and_age_message = True
        fetch_ages_message = True
        # bull_used is a list of the IDs of the bulls that have been used so far.
        bull_used = []
        herd_counter = 0
        for herd in xrange(base_herds):
            bull_portfolio[herd] = []
            cow_portfolio[herd] = []

            herd_bulls = get_herd_bulls(bulls, recessives, generation, herd_counter, bull_criterion, bull_deficit,
                                        polled_sire_count_message, bull_copies,
                                        bull_unique, bull_used,
                                        fetch_rec_and_age_message, fetch_ages_message,
                                        base_herds, service_bulls, bull_mating_age, cow_mating_age, debug)
            # The list of bulls used is updated here and passed back into get_herd_bulls() when
            # the next herd is processed.
            bull_used += [b['animal'] for b in herd_bulls]
            # A list of only the cows that are of breeding age
            if debug and bull_unique:
                print '\t\t[compute_inbreeding]: Herd %s portfolio: %s' % ( herd, [b['animal'] for b in herd_bulls] )
            herd_counter += 1
            polled_sire_count_message = False
            fetch_rec_and_age_message = False
            fetch_ages_message = False

            herd_bulls.sort(key=lambda x: x['tbv'], reverse=True)  # Sort in descending order on TBV
            herd_bulls = herd_bulls[0:service_bulls]  # Keep the top "service_bulls" sires for use
            herd_cows = [c for c in aged_cows if c['herd'] == herd]
            # Now create proxy calves for each cow-bull combination.
            for b in herd_bulls:
                bull_portfolio[herd].append(b)
                for c in herd_cows:
                    if c not in cow_portfolio[herd]:
                        cow_portfolio[herd].append(c)
                    calf_id = str(b['animal']) + '__' + str(c['animal'])
                    if calf_id in id_list:
                        if debug:
                            print '\t\t[compute_inbreeding]: Error! A calf with ID %s already exists in the ID list in \
                                  generation %s!' % (calf_id, generation)
                    if pedigree_array:
                        pedigree[pedigree_counter] = (calf_id, b['sire'], c['dam'], generation + 10)
                    else:
                        pedigree.append(' '.join([calf_id, b['sire'], c['dam'], generation + 10, '\n']))
                    pedigree_counter += 1
                    calfcount += 1

        if debug:
            print '\t\t[compute_inbreeding]: %s calves added to pedigree in generation %s at %s' % \
                  (calfcount, generation, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            print '\t\t[compute_inbreeding]: %s total animals in pedigree in generation %s at %s' % \
                  (pedigree_counter, generation, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

    # Write the pedigree to a file.
    if len(filetag) == 0:
        if penalty:
            pedfile = 'compute_inbreeding_r_%s.txt' % generation
        else:
            pedfile = 'compute_inbreeding_r_%s.txt' % generation
    else:
        if penalty:
            pedfile = 'compute_inbreeding_r%s_%s.txt' % (filetag, generation)
        else:
            pedfile = 'compute_inbreeding%s_%s.txt' % (filetag, generation)
    if debug:
        print '\t[compute_inbreeding]: Writing pedigree to %s at %s' % \
              (pedfile, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    ofh = file(pedfile, 'w')
    if isinstance(pedigree, (np.ndarray, np.generic)):
        for pidx in xrange(pedigree_counter):
            p = ' '.join([pedigree[pidx][0], pedigree[pidx][1], pedigree[pidx][2], str(pedigree[pidx][3]), '\n'])
            ofh.write(p)
    else:
        for p in pedigree:
            ofh.write(p)
    ofh.close()
    del pedigree
    #
    #
    # INBREEDING CODE
    #
    #
    # PyPedal is just too slow when the pedigrees are large (e.g., millons of records), so
    # we're going to use Ignacio Aguilar's INBUPGF90 program.
    #
    # Per an e-mail from Ignacio Aguilar on 06/25/2014, INBUPGF90 does NOT emit a proper
    # status return code when it exits, which makes it tricky to know for sure when the
    # job is done. I've observed a number of cases where the simulation appears to stall
    # because subprocess.call() does not recognize that INBUPGF90 has finished a job. So,
    # I've cobbled-together a solution using ideas from Ezequiel Nicolazzi
    # (https://github.com/nicolazzie/AffyPipe/blob/master/AffyPipe.py) and a post on
    # Stack Overflow (http://stackoverflow.com/questions/12057794/
    # python-using-popen-poll-on-background-process). I'm not 100% sure that this works
    # as intended, but I'm out of ideas.
    if len(filetag) == 0:
        if penalty:
            logfile = 'compute_inbreeding_r_%s.log' % generation
        else:
            logfile = 'compute_inbreeding_%s.log' % generation
    else:
        if penalty:
            logfile = 'compute_inbreeding_r%s_%s.log' % (filetag, generation)
        else:
            logfile = 'compute_inbreeding%s_%s.log' % (filetag, generation)
    # Several methods can be used:
    # 1 - recursive as in Aguilar & Misztal, 2008 (default)
    # 2 - recursive but with coefficients store in memory, faster with large number of
    #     generations but more memory requirements
    # 3 - method as in Meuwissen & Luo 1992
    # * Method 3 seems to work quite well, methods 1 and 2 seem to "stall" sometimes, not sure why. *
    if debug:
        print '\t[compute_inbreeding]: Started inbupgf90 to calculate COI at %s' % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    callinbupgf90 = ['inbupgf90', '--pedfile', pedfile, '--method', '3', '--yob', '>', logfile, '2>&1&']
    time_waited = 0
    p = subprocess.Popen(callinbupgf90, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    while p.poll() is None:
        # Wait 1 second between pokes with a sharp stick.
        time.sleep(10)
        time_waited += 10
        p.poll()
        if time_waited % 60 == 0 and debug:
            print '\t\t[compute_inbreeding]: Waiting for INBUPGF90 to finish -- %s minutes so far...' % int(time_waited/60)
    # Pick-up the output from INBUPGF90
    (results, errors) = p.communicate()
    if debug:
        if errors == '':
            print '\t\t[compute_inbreeding]: INBUPGF90 finished without problems at %s!' % \
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            if debug:
                print '\t\t\t%s' % results
        else:
            print '\t\t[compute_inbreeding]: errors: %s' % errors
        print '\t[compute_inbreeding]: Finished inbupgf90 to calculate COI at %s' %\
              datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # Load the COI into a dictionary keyed by original animal ID
    if len(filetag) == 0:
        if penalty:
            coifile = 'compute_inbreeding_r_%s.txt.solinb' % generation
        else:
            coifile = 'compute_inbreeding_%s.txt.solinb' % generation
    else:
        if penalty:
            coifile = 'compute_inbreeding_r%s_%s.txt.solinb' % (filetag, generation)
        else:
            coifile = 'compute_inbreeding%s_%s.txt.solinb' % (filetag, generation)
    if debug:
        print '\t[compute_inbreeding]: Putting coefficients of inbreeding from %s.solinb in a dictionary at %s' \
            % (pedfile, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    inbr = {}
    ifh = open(coifile, 'r')
    for line in ifh:
        pieces = line.split()
        #inbr[int(pieces[0])] = float(pieces[1])
        inbr[pieces[0]] = float(pieces[1])
    ifh.close()

    # Now, assign the coefficients of inbreeding to the animal records
    if debug:
        print '\t[compute_inbreeding]: Writing coefficients of inbreeding to animal records at %s' \
            % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    # I *think* that animals with no offspring are getting dropped from the pedigree when inbupgf90 is doing its
    # thing
    for c in cows: c['inbreeding'] = inbr[str(c['animal'])]
    for dc in dead_cows: dc['inbreeding'] = inbr[str(dc['animal'])]
    for b in bulls: b['inbreeding'] = inbr[str(b['animal'])]
    for db in dead_bulls: db['inbreeding'] = inbr[str(db['animal'])]

    # Clean-up
    try:
        if generation != generations:
            if len(filetag) > 0:
                if penalty:
                    os.remove('compute_inbreeding_r%s_%s.txt' % (filetag, generation))
                    os.remove('compute_inbreeding_r%s_%s.txt.errors' % (filetag, generation))
                    os.remove('compute_inbreeding_r%s_%s.txt.inbavgs' % (filetag, generation))
                    os.remove('compute_inbreeding_r%s_%s.txt.solinb' % (filetag, generation))
                else:
                    os.remove('compute_inbreeding%s_%s.txt' % (filetag, generation))
                    os.remove('compute_inbreeding%s_%s.txt.errors' % (filetag, generation))
                    os.remove('compute_inbreeding%s_%s.txt.inbavgs' % (filetag, generation))
                    os.remove('compute_inbreeding%s_%s.txt.solinb' % (filetag, generation))
            else:
                if penalty:
                    os.remove('compute_inbreeding_r_%s.txt' % (generation))
                    os.remove('compute_inbreeding_r_%s.txt.errors' % (generation))
                    os.remove('compute_inbreeding_r_%s.txt.inbavgs' % (generation))
                    os.remove('compute_inbreeding_r_%s.txt.solinb' % (generation))
                else:
                    os.remove('compute_inbreeding_%s.txt' % (generation))
                    os.remove('compute_inbreeding_%s.txt.errors' % (generation))
                    os.remove('compute_inbreeding_%s.txt.inbavgs' % (generation))
                    os.remove('compute_inbreeding_%s.txt.solinb' % (generation))
    except OSError:
        print '\t[compute_inbreeding]: Unable to delete all inbreeding files.'

    # Send everything back to the calling routine
    return cows, bulls, dead_cows, dead_bulls, matings, bull_portfolio, cow_portfolio, inbr


# Return a list of bulls for mating to a herd of cows.
#
# 02/26/2019    JBC    Merged in MM changes.

def get_herd_bulls(bulls, recessives, generation, herd_counter, bull_criterion='random', bull_deficit='use_horned',
                   polled_sire_count_message=False, bull_copies=4, bull_unique=False,
                   bull_used=[], rec_msg=False, ages_msg=False, base_herds=100, service_bulls=50,
                   bull_mating_age=2, cow_mating_age=2, debug=False):

    """Return a list of bulls for mating to a herd of cows.

    :param bulls: A list of live bull records.
    :type bulls: list
    :param recessives: A dictionary of recessives in the population.
    :type recessives: dictionary
    :param generation: The current generation in the simulation.
    :type generation: int
    :param herd_counter: The current herd getting a portfolio.
    :type herd_counter: int
    :param bull_criterion: Criterion used to select the group of bulls for mating.
    :type bull_criterion: string
    :param bull_deficit: Manner of handling too few bulls for matings: 'use_horned' or 'no_limit'.
    :type bull_deficit: string
    :param polled_sire_count_message: Print debugging message about availability of polled sires.
    :type polled_sire_count_message: boolean
    :param bull_copies: Genotype of polled bulls selected for mating (0|1|2|4|5|6)
    :type bull_copies: integer
    :param bull_unique: Each bull portfolio should be unique.
    :type bull_unique: boolean
    :param bull_used: List of bulls already used for matings.
    :type bull_used: list
    :param rec_msg: Activate/deactivate debugging messages in fetch_rec_and_age().
    :type rec_msg: boolean
    :param ages_msg: Activate/deactivate debugging messages in fetch_ages().
    :type rec_msg: boolean
    :param base_herds: Number of herds in the population.
    :type base_herds: int
    :param service_bulls: Number of herd bulls to use in each herd each generation.
    :type service_bulls: int
    :param debug: Activate/deactivate debugging messages.
    :type debug: boolean
    :return: A list of bulls for mating to cows in a herd.
    :rtype: list
    """

    if bull_criterion not in ['random', 'polled']:
        if debug:
            print '\t\t[get_herd_bulls]: bull_criterion had an unrecognized value of %s, setting to \'random\'.' \
                  % bull_criterion
        bull_criterion = 'random'

    # Initialize herd_bulls
    herd_bulls = []

    # Get list of unique herds, but don't print the number for each herd
    herd_list = get_unique_herd_list(bulls, debug=False)
    # Herd_list starts at 0 so the count of herds is shorted by 1 if below 200 or 10 herds (not exactly sure why...)
    if len(herd_list) == 200:
        numberof_herds = len(herd_list)
    elif len(herd_list) == 10:
        numberof_herds = len(herd_list)
    else:
        numberof_herds = (len(herd_list) +1)

    # How many bulls are needed to breed entire max population to unique (non-overlapping) portfolios?
    bulls_needed = (service_bulls * numberof_herds)

    # A list of only the bulls that are of breeding age
    aged_bulls = fetch_ages(bulls, generation, bull_mating_age, cow_mating_age, messages=ages_msg, debug=debug)

    # How many bulls can (evenly) be placed in each herd?
    bulls_per_herd = int( math.floor(float(len(aged_bulls)) / numberof_herds) )

    # Use polled bulls
    if bull_criterion == 'polled':
        # Copies = 4 will select all PP and Pp bulls; copies = 0 will select only PP bulls
        # Also only select bulls of mating age
        mating_bulls = fetch_rec_and_age(bulls, generation, 'Horned', recessives, bull_mating_age, cow_mating_age,
                                         copies=bull_copies, messages=rec_msg, debug=debug)
        random.shuffle(mating_bulls)  # Randomly order bulls
        other_bulls = [bull for bull in aged_bulls if bull not in mating_bulls]
        random.shuffle(other_bulls)
        if debug and polled_sire_count_message:
            print '\t[get_herd_bulls]: Length of mating_bulls is (%s) and length of other bulls is (%s).' % \
            (len(mating_bulls), len(other_bulls))
            polled_sire_count_message = True

        # How many herds will get at least 1 polled bull
        polled_herds = int( math.ceil( float(len(mating_bulls)) / bulls_per_herd ) )

        # How many herds should get an extra bull
        remainder_herds = (len(aged_bulls) % numberof_herds)
        if remainder_herds < 0:
            remainder_herds = 0
        # In the herd counter, what herd number stops getting an extra bull
        nonremainder_herd_start = (polled_herds + remainder_herds)
        nonremainder_herds = (numberof_herds - polled_herds - remainder_herds)

        # There are not enough polled bulls for each herd. Have to decide how to handle the deficit next.
        if len(mating_bulls) < bulls_needed:

################## Polled: USE_HORNED #################################

            if bull_deficit == 'use_horned':
                # show the total number of polled sires that were found (only prints once)
                if debug and polled_sire_count_message:
                    print '\t[get_herd_bulls]: Fewer polled sires (%s) than needed (%s) for all (%s) herds, using horned sires, too. '\
                          'There are %s total aged_bulls available.' % (len(mating_bulls), bulls_needed, numberof_herds, len(aged_bulls))
                    print '\t[get_herd_bulls]: There will be  %s polled herds, %s remainder herds and %s NON-remainder herds' % \
                    (polled_herds, remainder_herds, nonremainder_herds)
                    polled_sire_count_message = True

                # If only unique bulls are desired then only take bulls that weren't already used. This may break
                # if there are fewer bulls in the population than (base_herds * service_bulls).
                if bull_unique:
                    # Select bulls from mating_bulls that have not yet been used for mating to this herd.'
                    # less bulls available per herd than required by service_bulls so use bulls_per_herd as floor
                    if bulls_per_herd < service_bulls:
                        herd_bulls = [b for b in mating_bulls if b['animal'] not in bull_used][0:bulls_per_herd]
                        # There are enough polled sires - no need for horned for this herd
                        if len(herd_bulls) == bulls_per_herd:
                            print '\t[get_herd_bulls]: Enough polled sires (%s) for this herd' % (len(herd_bulls))
                            return herd_bulls
                        # If there aren't enough polled bulls to complete the portfolio, but there are still some polled left,
                        # randomly sample other_bulls (i.e., horned).
                        elif len(herd_bulls) < bulls_per_herd and len(herd_bulls) > 0:
                            if debug:
                                print '\t[get_herd_bulls]: Fewer polled sires (%s) than needed (%s) for this herd, '\
                                'using (%s) horned sires too.' % (len(herd_bulls), bulls_per_herd, (bulls_per_herd - len(herd_bulls)))
                            # some polled left for this herd so keep the herd port the normal even length
                            herd_bulls = herd_bulls + [b for b in other_bulls if b['animal'] not in bull_used][
                                0:(bulls_per_herd - len(herd_bulls))]
                            return herd_bulls
                        # 0 polled so using all horned
                        elif len(herd_bulls) < bulls_per_herd and len(herd_bulls) == 0:
                            if remainder_herds > 0:
                                # the herd is not using polled and can have an additional remainder bull
                                if herd_counter < nonremainder_herd_start:
                                    if debug:
                                        print '\t[get_herd_bulls]: (%s) polled sires for this herd, so using all (%s)' \
                                        'horned sires (remainder herd).' % (len(herd_bulls), (bulls_per_herd-len(herd_bulls)+1))
                                    herd_bulls = herd_bulls + [b for b in other_bulls if b['animal'] not in bull_used][
                                        0:((bulls_per_herd+1)-len(herd_bulls))]
                                    return herd_bulls
                                # herd does not get a remainder bull
                                if herd_counter >= nonremainder_herd_start:
                                    if debug:
                                        print '\t[get_herd_bulls]: (%s) polled sires for this herd - need (%s), so '\
                                        'using all horned sires.' % (len(herd_bulls), bulls_per_herd)
                                    herd_bulls = herd_bulls + [b for b in other_bulls if b['animal'] not in bull_used][
                                        0:(bulls_per_herd - len(herd_bulls))]
                                    return herd_bulls
                            # there are no remainders
                            elif remainder_herds == 0:
                                # also the herd does not get a remainder bull
                                if debug:
                                    print '\t[get_herd_bulls]: (%s) polled sires for this herd - need (%s), so '\
                                    'using all horned sires.' % (len(herd_bulls), bulls_per_herd)
                                herd_bulls = herd_bulls + [b for b in other_bulls if b['animal'] not in bull_used][
                                    0:(bulls_per_herd - len(herd_bulls))]
                                return herd_bulls

                    # more bulls available per herd than required by service_bulls so use service_bulls as floor
                    else:
                        herd_bulls = [b for b in mating_bulls if b['animal'] not in bull_used][0:service_bulls]
                        # If there aren't enough polled bulls to complete the portfolio, randomly sample other_bulls (i.e., horned).
                        if len(herd_bulls) < service_bulls :
                            if debug:
                                print '\t[get_herd_bulls]: Fewer polled sires (%s) than needed (%s) for this herd, '\
                                'using horned sires, too.' % (len(herd_bulls), service_bulls)
                            herd_bulls = herd_bulls + [b for b in other_bulls if b['animal'] not in bull_used][
                                0:(service_bulls - len(herd_bulls))]
               # We can re-use bulls, so don't worry about uniqueness.
                else:
                    herd_bulls = mating_bulls + other_bulls[0:(service_bulls - len(mating_bulls))]

################## Polled: OPEN #################################

            elif bull_deficit == 'open':
                # How many polled bulls can (evenly) be placed in each herd?
                polled_per_herd = int( math.floor( float(len(mating_bulls))/ numberof_herds ) )
                # How many herds will get at least 1 polled bull
                # How many herds should get one of the remainder polled bulls
                polled_remainder_herds = len(mating_bulls) % numberof_herds

                if len(mating_bulls) >= numberof_herds:
                    open_herds = 0
                else:
                    open_herds = numberof_herds - polled_remainder_herds

                polled_herds2 = numberof_herds - polled_remainder_herds - open_herds

                # show the total number of polled sires that were found (only prints once)
                if debug and polled_sire_count_message:
                    print '\t[get_herd_bulls]: Fewer polled sires (%s) than needed (%s) for all (%s) herds, cows will be left open.' % \
                          (len(mating_bulls), bulls_needed, numberof_herds)
                    print '\t[get_herd_bulls]: There will be %s herds with (%s) polled bulls, %s herds with (%s) polled bull(s)'\
                    ' & %s open herds [%s non-useable horned bulls]' \
                    % (polled_herds2, polled_per_herd, polled_remainder_herds, (polled_per_herd+1), open_herds, len(other_bulls))
                    polled_sire_count_message = True

                # Select bulls from mating_bulls that have not yet been used for mating to this herd.
                if bull_unique:
                    # less bulls available per herd than required by service_bulls so use bulls_per_herd as floor
                    if polled_per_herd < service_bulls and open_herds == 0:
                        if herd_counter < polled_remainder_herds:
                            herd_bulls = [b for b in mating_bulls if b['animal'] not in bull_used][0:(polled_per_herd+1)]
                            print '\t[get_herd_bulls]: This (polled_remainder) herd gets %s polled bull(s)' % (polled_per_herd+1)
                        if herd_counter >= polled_remainder_herds:
                            herd_bulls = [b for b in mating_bulls if b['animal'] not in bull_used][0:polled_per_herd]
                            print '\t[get_herd_bulls]: This (polled) herd gets %s polled bull(s)' % (polled_per_herd)
                    elif polled_per_herd < service_bulls and open_herds > 0:
                        if herd_counter < polled_remainder_herds:
                            herd_bulls = [b for b in mating_bulls if b['animal'] not in bull_used][0:(polled_per_herd+1)]
                            print '\t[get_herd_bulls]: This (polled_remainder) herd gets %s polled bull(s)' % (polled_per_herd+1)
                        if herd_counter >= polled_remainder_herds:
                            herd_bulls = []
                            print '\t[get_herd_bulls]: This is an open herd with 0 polled bulls'
                    # more bulls available per herd than required by service_bulls so use service_bulls as floor
                    elif polled_per_herd > service_bulls:
                        herd_bulls = [b for b in mating_bulls if b['animal'] not in bull_used][0:service_bulls]
               # We can re-use bulls, so don't worry about uniqueness.
                else:
                    herd_bulls = mating_bulls[0:service_bulls]  # Select a portfolio at random

################## Polled: NO_LIMIT #################################

            if bull_deficit == 'no_limit':
                # show the total number of polled sires that were found (only prints once)
                if debug and polled_sire_count_message:
                    print '\t[get_herd_bulls]: Fewer polled sires (%s) than needed (%s) for all (%s) herds, but bull_deficit is set' \
                    'to \'no_limit\', so the number of matings allowed for a bull may exceed max number of matings.' \
                    % (len(mating_bulls), bulls_needed, numberof_herds)
                # No limit represents AI so unique natural service portfolios are not necessary (i.e., bulls can be re-used)
                if int(len(aged_bulls) / 5) + 1 < service_bulls:
                    herd_bulls = mating_bulls[0:service_bulls]  # Select service_bulls at random
                else:
                    herd_bulls = mating_bulls[0:int(len(mating_bulls) / 5)]  # Select 20% at random
                if debug:
                    print '\t[get_herd_bulls]: There will be %s (non-unique) bulls in this herd.' % len(herd_bulls)

################## Polled: NO Deficit #################################

        # More polled bulls than bulls_needed so don't have to deal with a deficit
        else:
            if bull_unique:
                # more bulls available per herd than required by service_bulls so use service_bulls as floor
                herd_bulls = [b for b in mating_bulls if b['animal'] not in bull_used][0:service_bulls]
                return herd_bulls
                print '\t[get_herd_bulls]: Enough unique polled sires (%s) for this herd' % (len(herd_bulls))
            # We don't care about unique portfolios
            else:
                if int(len(aged_bulls) / 5) + 1 < service_bulls:
                    herd_bulls = mating_bulls[0:service_bulls]  # Select service_bulls at random
                else:
                    herd_bulls = mating_bulls[0:int(len(mating_bulls) / 5)]  # Select 20% at random
                if debug:
                    print '\t[get_herd_bulls]: There will be %s (non-unique) polled bulls in this herd.' % len(herd_bulls)

################## RANDOM #################################

    # The default case is 'random'.
    elif bull_criterion == 'random':
        # How many herds should get an extra bull
        remainder_herds = (len(aged_bulls) % numberof_herds)
        if remainder_herds < 0:
            remainder_herds = 0
        # How many herds don't get an extra bull
        nonremainder_herds = (numberof_herds - remainder_herds)

        random.shuffle(bulls)

        if debug and polled_sire_count_message:
            if bulls_per_herd < service_bulls:
                print '\t[get_herd_bulls]: There will be %s remainder herds with %s bulls and (%s) NON-remainder herds with (%s) buls.'\
                ' There are %s total aged bulls available, for %s herds.' \
                % (remainder_herds, (bulls_per_herd+1), nonremainder_herds, bulls_per_herd, len(aged_bulls), numberof_herds)
            # service_bulls > bulls_per_herd so the herd portfolios size will be what was set for service_bulls
            else:
                print '\t[get_herd_bulls]: There will be %s bulls per herd (max service_bulls).' % service_bulls
            polled_sire_count_message = True
        if bull_unique:
            # less bulls available per herd than required by service_bulls so use bulls_per_herd as floor
            if bulls_per_herd < service_bulls:
                # These herds get an extra (remainder) bull
                if herd_counter < remainder_herds:
                    herd_bulls = [b for b in aged_bulls if b['animal'] not in bull_used][0:(bulls_per_herd+1)]
                # These herds DO NOT get an extra (remainder) bull
                if herd_counter >= remainder_herds:
                    herd_bulls = [b for b in aged_bulls if b['animal'] not in bull_used][0:bulls_per_herd]
            # more bulls available per herd than required by service_bulls so use service_bulls as floor
            else:
                herd_bulls = [b for b in aged_bulls if b['animal'] not in bull_used][0:service_bulls]
            if debug:
                print '\t[get_herd_bulls]: There will be %s unique bulls in this herd.' % len(herd_bulls)
        # Don't care about unique herd portfolios, re-use is okay
        else:
            # Sample 20% of the active bulls at random, then sort them on TBV and take the top "service_bulls" bulls
            # for use in the herd.
            # In some scenarios, there are only a few. When that happens, 20% can be less than service bulls, so we're
            # going to use service_bulls as a floor.
            if int(len(aged_bulls) / 5) + 1 < service_bulls:
                herd_bulls = aged_bulls[0:service_bulls]  # Select service_bulls at random
            else:
                herd_bulls = aged_bulls[0:int(len(aged_bulls) / 5)]  # Select 20% at random
            if debug:
                print '\t[get_herd_bulls]: There will be %s (non_unique) bulls in this herd.' % len(herd_bulls)
    else:
        if debug:
            print '\t[polled_mating]: Unhandled value of bull_criterion, %s, returning empty list!' % bull_criterion
        return []

    return herd_bulls

# This routine uses an approach similar to that of Pryce et al. (2012) allocate matings of bulls
# to cows. Parent averages are discounted for any increase in inbreeding in the progeny, and
# they are further discounted to account for the effect of recessives on lifetime income.


def pryce_mating(cows, bulls, dead_cows, dead_bulls, generation, generations, filetag,
                 recessives, max_matings=500, base_herds=100, debug=False,
                 penalty=False, service_bulls=50, edit_prop=[0.0,0.0], edit_type='C',
                 edit_trials=1, embryo_trials=1, embryo_inbreeding=False, edit_sex='M',
                 flambda=25., bull_criterion='random', bull_deficit='use_horned',
                 carrier_penalty=False, bull_copies=4, bull_unique=False, calf_loss=0.0,
                 dehorning_loss=0.0, harvest_beef=0.0, bull_mating_age=2,
                 cow_mating_age=2, extras=()):

    """Allocate matings of bulls to cows using Pryce et al.'s (2012) or Cole's (2015) method.

    :param cows: A list of live cow records.
    :type cows: list
    :param bulls: A list of live bull records.
    :type bulls: list
    :param dead_cows: A list of dead cow records.
    :type dead_cows: list
    :param dead_bulls: A list of dead bull records.
    :type dead_bulls: list
    :param generation: The current generation in the simulation.
    :type generation: int
    :param generations: The total number of generations in the simulation.
    :type generations: int
    :param filetag: Prefix used for filenames to tell scenarios apart.
    :type filetag: string
    :param recessives: A dictionary of recessives in the population.
    :type recessives: dictionary
    :param max_matings: The maximum number of matings permitted for each bull
    :type max_matings: int
    :param base_herds: Number of herds in the population.
    :type base_herds: int
    :param bull_mating_age: The minimum age for bulls to breed cows.
    :type generation: int
    :param cow_mating_age: The minimum age for cows to be bred and have a calf in the same generation.
    :type generation: int
    :param debug: Activate/deactivate debugging messages.
    :type debug: True or False
    :param penalty: Boolean. Adjust PA for recessives, or adjust only for inbreeding
    :type penalty: bool
    :param service_bulls: Number of herd bulls to use in each herd each generation.
    :type service_bulls: int
    :param edit_prop: The proportion of animals to edit based on TBV (e.g., 0.01 = 1 %).
    :type edit_prop: list
    :param edit_type: Tool used to edit genes: 'Z' = ZFN, 'T' = TALEN, 'C' = CRISPR, 'P' = no errors.
    :type edit_type: char
    :param edit_trials: The number of attempts to edit an embryo successfully (-1 = repeat until success).
    :type edit_trials: int
    :param embryo_trials: The number of attempts to transfer an edited embryo successfully (-1 = repeat until success).
    :type embryo_trials: int
    :param embryo_inbreeding: Write a file of coefficients of inbreeding for all possible bull-by-cow matings.
    :type embryo_inbreeding: boolean
    :param flambda: Decrease in economic merit (in US dollars) per 1% increase in inbreeding.
    :type flambda: float
    :param bull_criterion: Criterion used to select the group of bulls for mating.
    :type bull_criterion: string
    :param bull_deficit: Manner of handling too few bulls for matings: 'use_horned' or 'no_limit'.
    :type bull_deficit: string
    :param carrier_penalty: Penalize carriers for carrying a copy of an undesirable allele (True), or not (False)
    :rtype carrier_penalty: bool
    :param bull_copies: Genotype of polled bulls selected for mating (0|1|2|4|5|6)
    :type bull_copies: integer
    :param bull_unique: Each bull portfolio should be unique.
    :type bull_unique: boolean
    :param calf_loss: Proportion of calves that die before they reach 1 year of age.
    :type calf_loss: float
    :param dehorning_loss: The proportion of cows that die during dehorning.
    :type dehorning_loss: float
    :param extras: List(s) of "extra" animals to be used for assigning IDs when nucleus herds are used.
    :type extras: tuple
    :return: Separate lists of cows, bulls, dead cows, and dead bulls.
    :rtype: list
    """

    if debug:
        print '\t[pryce_mating]: Parameters:\n\t\tgeneration: %s\n\t\tmax_matings: %s\n\t\tbase_herds: ' \
              '%s\n\t\tdebug: %s\n\t\tservice_bulls: %s\n\t\tpenalty: %s\n\t\tRecessives:' % (generation, max_matings,
                                                                                              base_herds, debug,
                                                                                              service_bulls, penalty)
        for r in recessives:
            print '\t\t\t%s' % r
        print
    # Never trust users, they are lying liars
    if max_matings < 0:
        print '\t[pryce_mating]: %s is less than 0, changing num_matings to 500.' % max_matings
        max_matings = 500
    if not type(max_matings) is int:
        print '\t[pryce_mating]: % is not not an integer, changing num_matings to 500.' % max_matings

    #
    # Compute inbreeding
    #
    cows, bulls, dead_cows, dead_bulls, matings, bull_portfolio, cow_portfolio, inbr = compute_inbreeding(cows=cows,
                                                                                                          bulls=bulls,
                                                                                                          dead_cows=dead_cows,
                                                                                                          dead_bulls=dead_bulls,
                                                                                                          generation=generation,
                                                                                                          generations=generations,
                                                                                                          filetag=filetag,
                                                                                                          penalty=penalty,
                                                                                                          proxy_matings=True,
                                                                                                          bull_criterion=bull_criterion,
                                                                                                          bull_deficit=bull_deficit,
                                                                                                          bull_copies=bull_copies,
                                                                                                          bull_unique=bull_unique,
                                                                                                          base_herds=base_herds,
                                                                                                          service_bulls=service_bulls,
                                                                                                          debug=debug,)



    next_id = get_next_id(cows, bulls, dead_cows, dead_bulls, *extras)

    #
    # ASSIGN MATINGS CODE
    #
    # We want to save F_ij and \sum{P(aa)} for individual matings for later analysis.
    if penalty:
        fpdict = {}

    #flambda = 25.           # Loss of NM$ per 1% increase in inbreeding
    if debug:
        print '\t[pryce_mating]: Starting loop over herds to identify optimal matings at %s' % \
              datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    tenth = len(bull_portfolio.keys()) / 10
    if tenth <= 0:
        tenth = 1
    herd_counter = 0
    # For each herd we're going to loop over all possible matings of the cows in the herd to the randomly chosen
    # bull portfolio and compute a parent average. Then we'll select the actual matings. This will be on a within-
    # herd basis, so a new B and M will be computed for each herd.
    new_bulls = []
    new_cows = []

    # Set all cows open to start the next round of mating
    for c in cows:
        c['bred'] = 0
    print '\t[pryce_mating]: Setting all cows to open before mating'
    print '\t[pryce_mating]: Harvest beef rate is (%s)' % (harvest_beef)
    for h in bull_portfolio.keys():
        herd_counter += 1
        if herd_counter % tenth == 0 and debug:
            print '\t\t[pryce_mating]: Processing herd %s of %s at %s' % \
              (herd_counter, len(bull_portfolio.keys()), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
        # We need these lists so that we can step into the correct locations in the relationship matrix to get the
        # relationship of each cow to each bull.
        bids = [str(b['animal']) for b in bull_portfolio[h]]
        cids = [str(c['animal']) for c in cow_portfolio[h]]
        # Setup the B_0 matrix, which will contain PA BV plus an inbreeding penalty
        b_mat = ma.zeros((len(bull_portfolio[h]), len(cow_portfolio[h])))
        # Setup the F matrix, which will contain inbreeding coefficients
        f_mat = ma.zeros((len(bull_portfolio[h]), len(cow_portfolio[h])))
        # Setup the M matrix, which will contain the actual matings
        m_mat = ma.zeros((len(bull_portfolio[h]), len(cow_portfolio[h])))
        # Now process the herd the first time, to compute PA.
        for b in bull_portfolio[h]:
            bidx = bids.index(str(b['animal']))
            # print 'Bull idx: ', bidx, ' (', str(b[0]), ')'
            for c in cow_portfolio[h]:
                cidx = cids.index(str(c['animal']))
                # print '\tCow idx : ', bidx, ' (', str(c[0]), ')'
                calf_id = str(b['animal'])+'__'+str(c['animal'])
                # Set accumulator of \sum P(aa) to 0.
                paa_sum = 0.
                # Update the matrix of inbreeding coefficients.
                f_mat[bidx, cidx] = inbr[calf_id]
                # Now adjust the PA to account for inbreeding and the economic impacts of the recessives.
                b_mat[bidx, cidx] = (0.5 * (b['tbv'] + c['tbv'])) - (inbr[calf_id] * 100 * flambda)
                # Adjust the PA of the mating to account for recessives on. If the flag is not set then
                # results should be similar to those of Pryce et al. (2012).
                if penalty:
                    for rk, rv in recessives.iteritems():
                        r = recessives.keys().index(rk)
                        # What are the parent genotypes?
                        b_gt = b['genotypes'][r]
                        c_gt = c['genotypes'][r]
                        if b_gt == -1 and c_gt == -1:           # aa genotypes
                            # Affected calf, adjust the PA by the full value of an aa calf.
                            b_mat[bidx, cidx] -= rv['value']
                            paa_sum += 1.
                        elif b_gt == 1 and c_gt == 1:           # AA genotypes
                            # Calf cannot be aa, no adjustment to the PA.
                            pass
                        elif ( b_gt == 1 and c_gt == 0 ) or ( b_gt == 0 and c_gt == 1 ):
                            # AA x Aa -> AA:Aa in the offspring
                            #
                            # We may want to penalize matings which produce carriers, in the long term.
                            # So, let's try assigning a value to a minor allele equal to 1/2 of the
                            # cost of a recessive. We then multiply that by 1/2 because only 1/2 of the
                            # offspring will be carriers. This gives us:
                            #
                            #       total penalty   = (1/2 * 1/2) * penalty
                            #                       = 1/4 * penalty.
                            if carrier_penalty:
                                b_mat[bidx, cidx] -= (0.25 * rv['value'])
                        elif ( b_gt == 1 and c_gt == -1 ) or ( b_gt == -1 and c_gt == 1 ):
                            # AA x aa -> Aa in the offspring
                            #
                            # Assign a value to a minor allele equal to 1/2 of the cost of a recessive.
                            # We then multiply that by 1 because all of the offspring will be carriers.
                            # This gives us:
                            #
                            #       total penalty   = 1/2 * penalty.
                            if carrier_penalty:
                                b_mat[bidx, cidx] -= (0.5 * rv['value'])
                        elif ( b_gt == 0 and c_gt == -1 ) or ( b_gt == -1 and c_gt == 0 ):
                            # Aa x aa -> Aa:aa in the offspring
                            #
                            # Assign a value to a minor allele equal to 1/2 of the cost of a recessive.
                            # We then multiply that by 1/2 because half of the offspring will be carriers.
                            # This gives us:
                            #
                            #       total penalty   = 1/4 * penalty (carriers) + 1/2 * penalty (affected).
                            if carrier_penalty:
                                b_mat[bidx, cidx] -= ( (0.25 * rv['value']) + (0.50 * rv['value']) )
                        else:                                   # Aa * Aa matings
                            # We may want to penalize matings which produce carriers, in the long term.
                            # So, let's try assigning a value to a minor allele equal to 1/2 of the
                            # cost of a recessive. We then multiply that by 1/2 because only 1/2 of the
                            # offspring will be carriers. After that, deduct the 1/4 penalty for the homo-
                            # zygotes. This leads us to: total penalty = (1/2 * 1/2) * penalty + 1/4 * penalty =
                            # 1/2 * penalty.
                            if carrier_penalty:
                                b_mat[bidx, cidx] -= (0.5 * rv['value'])
                            # There is a 1/4 chance of having an affected calf,
                            # so the PA is adjusted by 1/4 of the "value" of an
                            # aa calf.
                            else:
                                b_mat[bidx, cidx] -= (0.25 * rv['value'])
                            paa_sum += 0.25
                    # Store the inbreeding/P(aa) info for later. We're saving only calves because they're the animals
                    # for which we sum the P(aa) to make mating decisions.
                    fpdict[calf_id] = {}
                    fpdict[calf_id]['sire'] = str(b['animal'])
                    fpdict[calf_id]['dam'] = str(c['animal'])
                    fpdict[calf_id]['gen'] = generation
                    fpdict[calf_id]['inbr'] = inbr[calf_id]
                    fpdict[calf_id]['paa'] = paa_sum
                    fpdict[calf_id]['mating'] = 0

        #
        # From Pryce et al. (2012) (http://www.journalofdairyscience.org/article/S0022-0302(11)00709-0/fulltext#sec0030)
        # A matrix of selected mates (mate allocation matrix; M) was constructed, where Mij=1 if the corresponding
        # element, Bij was the highest value in the column Bj; that is, the maximum value of all feasible matings for
        # dam j, all other elements were set to 0, and were rejected sire and dam combinations.
        #

        # Sort bulls on ID in ascending order
        bull_portfolio[h].sort(key=lambda x: x['animal'])
        cow_id_list = [c['animal'] for c in cow_portfolio[h]]
        if len(cow_id_list) > ( service_bulls * max_matings ) and bull_deficit != 'no_limit':
            print '\t[pryce_mating]: WARNING! There are %s cows in herd %s, but %s service sires limited to %s matings ' \
                'cannot breed that many cows! Only the first %s cows in the herd will be bred, the other %s will be ' \
                'left open.' % (len(cow_id_list), h, service_bulls, max_matings, (service_bulls*max_matings),
                                (len(cow_id_list)-(service_bulls*max_matings)))
        elif len(cow_id_list) > ( service_bulls * max_matings ) and bull_deficit == 'no_limit':
            print '\t[pryce_mating]: WARNING! There are %s cows in herd %s, but %s service sires limited to %s matings ' \
                'cannot breed that many cows! The bull_deficit option is set to \'no_limit\', so the number of matings ' \
                'allowed for a bull may exceed %s.' % (len(cow_id_list), h, service_bulls, max_matings, max_matings)
        else:
            pass
        # Now loop over B to allocate the best matings
        for c in cow_portfolio[h]:
            # What column in b_mat corresponds to cow c?
            cow_loc = cow_id_list.index(c['animal'])
            # Get a vector of indices that would result in a sorted list.
            sorted_bulls = ma.argsort(b_mat[:, cow_loc])
            # The first element in sorted_bulls is the index of the smallest element in b_mat[:,cow_loc]. The
            # last element in sorted_bulls is the index of the largest element in b_mat[:,cow_loc].
            for bidx in xrange(len(bull_portfolio[h])-1, -1, -1):
                # Does this bull still have matings available?
                if matings[bull_portfolio[h][sorted_bulls[bidx]]['animal']] >= max_matings and bull_deficit != 'no_limit':
                    pass
                    #print 'Bull %s (%s) already has %s matings.' % (bidx, str(bull_portfolio[h][sorted_bulls[bidx]][0]), matings[bull_portfolio[h][sorted_bulls[bidx]][0]])
                elif bull_portfolio[h][sorted_bulls[bidx]]['alive'] != 'A':
                    #print 'Bull %s (%s) is dead' % (bidx, str(bull_portfolio[h][sorted_bulls[bidx]][0]))
                    pass
                else:
                    m_mat[sorted_bulls[bidx], cow_loc] = 1
                    matings[bull_portfolio[h][sorted_bulls[bidx]]['animal']] += 1
                    calf = create_new_calf(bull_portfolio[h][sorted_bulls[bidx]], c, recessives, next_id,
                                           generation, calf_loss, dehorning_loss, harvest_beef, debug=debug)
                    calf_id = str(bull_portfolio[h][sorted_bulls[bidx]]['animal'])+'__'+str(c['animal'])
                    # Assign inbreeding to calf
                    calf['inbreeding'] = inbr[calf_id]
                    if penalty:
                        fpdict[calf_id]['mating'] = 1
                    if calf['sex'] == 'F': new_cows.append(calf)
                    else: new_bulls.append(calf)
                    next_id += 1
                    # only want to mark cows that actually got mated as bred
                    c['bred'] = 1
                    # ...and, we're done.
                    break

    # Write the F_ij / \sum{P(aa)} information that we've been accumulating to a file for later analysis.
    # Note that these files can be very large, and one is written out for EACH round (generation) of the
    # simulation!
    if embryo_inbreeding:
        fpfile = 'fij_paa_pryce_%s.txt' % generation
        fph = open(fpfile, 'w')
        for fpkey in fpdict.keys():
            fpline = '%s %s %s %s %s %s %s\n' % (fpkey, fpdict[fpkey]['sire'], fpdict[fpkey]['dam'],
                                                 fpdict[fpkey]['gen'], fpdict[fpkey]['inbr'],
                                                 fpdict[fpkey]['paa'], fpdict[fpkey]['mating']
                                                 )
            fph.write(fpline)
        fph.close()

    if debug:
        print '\t[pryce_mating]: Finished assigning mates and updating M_0 at %s' % \
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        print '\t\t[pryce_mating]: %s animals in original cow list' % len(cows)
        print '\t\t[pryce_mating]: %s animals in new cow list' % len(new_cows)
        print '\t\t[pryce_mating]: %s animals in original bull list' % len(bulls)
        print '\t\t[pryce_mating]: %s animals in new bull list' % len(new_bulls)

    # Add the newly born and dead animals to the appropriate lists.
    for nc in new_cows:
        if nc['alive'] == 'A':
            cows.append(nc)
        else:
            dead_cows.append(nc)
    for nb in new_bulls:
        if nb['alive'] == 'A':
            bulls.append(nb)
        else:
            dead_bulls.append(nb)

    # If gene editing is going to happen, it happens here
    if debug:
        print '\t\t[pryce_matings]: Do we edit? %s' % [rv['edit'] for rv in recessives.values()]
    if 1 in [int(rv['edit']) for rv in recessives.values()]:
        if edit_prop[0] > 0.0:
            if debug:
                print '\t\t[pryce_mating]: About to edit bulls. Next ID = %s' % \
                      get_next_id(cows, bulls, dead_cows, dead_bulls, *extras)
            cows, bulls, dead_cows, dead_bulls = edit_genes(cows, bulls, dead_cows, dead_bulls,
                                                            recessives, generation, edit_prop[0],
                                                            edit_type, edit_trials, embryo_trials,
                                                            edit_sex='M', debug=debug, extras=extras)
        if edit_prop[1] > 0.0:
            if debug:
                print '\t\t[pryce_mating]: About to edit cows. Next ID = %s' % \
                      get_next_id(cows, bulls, dead_cows, dead_bulls, *extras)
            cows, bulls, dead_cows, dead_bulls = edit_genes(cows, bulls, dead_cows, dead_bulls,
                                                            recessives, generation, edit_prop[1],
                                                            edit_type, edit_trials, embryo_trials,
                                                            edit_sex='F', debug=debug, extras=extras)
    # End of gene editing section

    if debug:
        print '\t\t[pryce_mating]: %s animals in final live cow list' % len(cows)
        print '\t\t[pryce_mating]: %s animals in final dead cow list' % len(dead_cows)
        print '\t\t[pryce_mating]: %s animals in final live bull list' % len(bulls)
        print '\t\t[pryce_mating]: %s animals in final dead bull list' % len(dead_bulls)

    return cows, bulls, dead_cows, dead_bulls

def fetch_rec_and_age(animal_list, generation, get_recessive, recessives, bull_mating_age=1, cow_mating_age=1, copies=0,
                      messages=False, debug=False):
    """Loop through the provided list of animals and create a new list of animals with COPIES
    number of the minor allele and only animals that are of mating age.

    :param animal_list: List of animal records.
    :type animal_list: list
    :param get_recessive: Recessive for which list will be returned.
    :type get_recessive: string
    :param recessives: dictionary of recessives in the population.
    :type recessives: dictionary
    :param bull_mating_age: The minimum age for bulls to breed cows.
    :type generation: int
    :param cow_mating_age: The minimum age for cows to be bred and have a calf in the same generation.
    :type generation: int
    :param copies: Boolean. Copies of the minor allele in selected animals (4 = A\_ and 5 = a\_).
    :type copies: int
    :param messages: Activate/deactivate debugging messages in fetch_rec_and_age().
    :type messages: boolean
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :return: List of animal records.
    :rtype: list
    """

    selected_animals = []

    # Make sure animals were provided.
    if len(animal_list) < 1:
        print '\t[fetch_rec_and_age]: The list of animals provided included %s records!' % \
              ( len(animal_list) )
        return []

    # Check to see if the requested recessive exists.
    if not get_recessive in recessives.keys():
        print '\t[fetch_rec_and_age]: The requested recessive, %s, is not in the list of recessives!' % \
              ( get_recessive )
        return []

    # Make sure the count of copies of the minor allele is valid.
    if copies not in [0,1,2,4,5,6]:
        print '\t[fetch_rec_and_age]: The number of copies, %s, is not 0, 1, 2, 4, 5, or 6!' % \
              (copies)
        return []

    # In the recessives array, a 1 indicates an AA, 0 is an Aa, and a -1 is aa.
    # fetch_rec_and_age() also uses 4 to select both AA & Aa, 5 to select both
    # aa & Aa, and 6 to select both AA & aa.

    # Where in the recessives list is the one we want?
    rec_loc = recessives.keys().index(get_recessive)

    for animal in animal_list:
        age = generation - animal['born']
        # Only look for bulls that are at least __ yrs of age
        if animal['sex'] == 'M' and age >= bull_mating_age:
            # Add AA animals if 0 copies of minor allele requested
            if animal['genotypes'][rec_loc] == 1 and copies == 0:
                selected_animals.append(animal)
            # Add Aa animals if 1 copy of minor allele requested
            elif animal['genotypes'][rec_loc] == 0 and copies == 1:
                selected_animals.append(animal)
            # Add aa animals if 2 copies of minor allele requested
            elif animal['genotypes'][rec_loc] == -1 and copies == 2:
                selected_animals.append(animal)
            elif (animal['genotypes'][rec_loc] == 0 or animal['genotypes'][rec_loc] == 1) and copies == 4:
                selected_animals.append(animal)
            elif (animal['genotypes'][rec_loc] == 0 or animal['genotypes'][rec_loc] == -1) and copies == 5:
                selected_animals.append(animal)
            elif (animal['genotypes'][rec_loc] == -1 or animal['genotypes'][rec_loc] == 1) and copies == 6:
                selected_animals.append(animal)
            else:
                pass
        # Only look for cows that are at least __ yrs of age
        # 03/06/2019    JBC    Sex code test was for 'M', should have been 'F'?
        elif animal['sex'] == 'F' and age >= cow_mating_age:
            # Add AA animals if 0 copies of minor allele requested
            if animal['genotypes'][rec_loc] == 1 and copies == 0:
                selected_animals.append(animal)
            # Add Aa animals if 1 copy of minor allele requested
            elif animal['genotypes'][rec_loc] == 0 and copies == 1:
                selected_animals.append(animal)
            # Add aa animals if 2 copies of minor allele requested
            elif animal['genotypes'][rec_loc] == -1 and copies == 2:
                selected_animals.append(animal)
            elif (animal['genotypes'][rec_loc] == 0 or animal['genotypes'][rec_loc] == 1) and copies == 4:
                selected_animals.append(animal)
            elif (animal['genotypes'][rec_loc] == 0 or animal['genotypes'][rec_loc] == -1) and copies == 5:
                selected_animals.append(animal)
            elif (animal['genotypes'][rec_loc] == -1 or animal['genotypes'][rec_loc] == 1) and copies == 6:
                selected_animals.append(animal)
            else:
                pass

    if messages:
        print '\t\t[fetch_rec_and_age]: %s animals selected for age (%s) and (%s) copies of recessive %s!' % \
              ( len(selected_animals), bull_mating_age, copies, get_recessive )

    return selected_animals


def fetch_ages(animal_list, generation, bull_mating_age=1, cow_mating_age=1, messages=False, debug=False):
    """Loop through the provided list of animals and create a new list of animals that are of mating age.

    :param animal_list: List of animal records.
    :type animal_list: list
    :param generation: The current generation in the simulation.
    :type generation: int
    :param bull_mating_age: The minimum age for bulls to breed cows.
    :type generation: int
    :param cow_mating_age: The minimum age for cows to be bred and have a calf in the same generation.
    :type generation: int
    :param messages: Activate/deactivate debugging messages in fetch_ages().
    :type messages: boolean
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :return: List of animal records.
    :rtype: list
    """

    aged_animals = []

    # Make sure animals were provided.
    if len(animal_list) < 1:
        print '\t[fetch_ages]: The list of animals provided included %s records!' % \
              ( len(animal_list) )
        return []

    for animal in animal_list:
        age = generation - animal['born']
        # Add bulls that are at least 2 yrs of age
        if animal['sex'] == 'M' and age >= bull_mating_age:
            aged_animals.append(animal)
        elif animal['sex'] == 'F' and age >= cow_mating_age:
            aged_animals.append(animal)
        else:
            pass

    if messages:
        print '\t\t[fetch_ages]: %s animals selected for mating age (%s-bulls) or (%s-cows).' % \
        (len(aged_animals), bull_mating_age, cow_mating_age)

    return aged_animals


# I finally had to refactor the create-a-calf code into its own subroutine. This function
# returns a new animal record.
#
# sire          : The sire's ID
# dam           : The dam's ID
# recessives    : A Python list of recessives in the population
# calf_id       : The ID to be assigned to the new animal
# generation    : The current generation in the simulation
# calf_loss     : Proportion of calves that die before they reach 1 year of age
# debug         : Flag to activate/deactivate debugging messages


def create_new_calf(sire, dam, recessives, calf_id, generation, calf_loss=0.0, dehorning_loss=0.0, harvest_beef=0.0,
                    debug=False):
    """Create and return a new calf record.

    :param sire: The father of the new animal.
    :type sire: int
    :param dam: The mother of the new animal.
    :type dam: int
    :param recessives: A dictionary of recessives in the population.
    :type recessives: dictionary
    :param calf_id: ID of the calf to create.
    :type calf_id: int
    :param generation: The current generation in the simulation.
    :type generation: int
    :param calf_loss: Proportion of calves that die before they reach 1 year of age.
    :type calf_loss: float
    :param dehorning_loss: The proportion of calves that die during dehorning.
    :type dehorning_loss: float
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :return: New animal record.
    :rtype: list
    """

    # Check calf_loss for permissible values
    if calf_loss >= 0.0 and calf_loss <= 1.0:
        pass
    else:
        if debug:
            print '\t[create_new_calf]: calf_loss has a value -- %s -- that is <0.0 or >1.0. Setting to 0.0.' % calf_loss
        calf_loss = 0.0
    # Is it a bull or a heifer?
    if bernoulli.rvs(0.50):
        sex = 'M'
    else:
        sex = 'F'
    # Compute the parent average
    tbv = (sire['tbv'] + dam['tbv']) * 0.5
    # Add a Mendelian sampling term.
    var_adj = math.sqrt(0.5) * ( 1. - ( 0.5 * ( sire['inbreeding'] + dam['inbreeding'] ) ) )
    tbv += (random.normalvariate(0, 1) * 200 * var_adj)
    # Form the animal record. Note that heifers are born into the same herd as their
    # dam. The record is laid out as follows:
    #     0  = animal ID
    #     1  = sire ID
    #     2  = dam ID
    #     3  = time (generation) born
    #     4  = sex of calf
    #     5  = herd in which calf was born
    #     6  = alive/dead flag
    #     7  = reason for death
    #     8  = time (generation) died
    #     9  = true breeding value
    #     10 = coefficient of inbreeding
    #     11 = edit status (have recessives been edited)
    #     12 = number of edits required for success
    #     13 = number of ETs required for success
    #     14 = reproductive status (0 = not bred last cycle, 1 = bred last cycle)
    #     15 = recessive genotypes
    #calf = [calf_id, sire[0], dam[0], generation, sex, dam[5], 'A', '', -1, tbv, 0.0, [], [], [], 0, []]
    calf = {'animal': calf_id, 'sire': sire['animal'], 'dam': dam['animal'], 'born': generation,
            'sex': sex, 'herd': dam['herd'], 'alive': 'A', 'reason': '',
            'died': -1, 'tbv': tbv, 'inbreeding': 0.0, 'edit_status': [],
            'edit_count': [], 'et_count': [], 'bred': 0, 'genotypes': []}
    # Check the bull and cow genotypes to see if the mating is at-risk
    # If it is, then reduce the parent average by the value of the recessive.
    c_gt = dam['genotypes']
    b_gt = sire['genotypes']
    edit_status = []                            # Indicates if an animal has been gene edited
    for rk, rv in recessives.iteritems():
        r = recessives.keys().index(rk)
        # The simplest way to do this is to draw a gamete from each parent and
        # construct the calf's genotype from there. In the recessives array, a
        # 1 indicates an AA, 0 is an Aa, and a -1 is aa.
        #
        # Draw an allele from the sire -- a 0 is an "A", and a 1 is an "a".
        if b_gt[r] == 1:                       # AA genotype
            s_allele = 'A'
        elif b_gt[r] == 0:                     # Aa genotype
            s_allele = bernoulli.rvs(0.5)
            if s_allele == 0:
                s_allele = 'A'
            else:
                s_allele = 'a'
        else:                                  # aa genotype
            s_allele = 'a'
        # Draw an allele from the dam -- a 0 is an "A", and a 1 is an "a".
        if c_gt[r] == 1:                       # AA genotype
            d_allele = 'A'
        elif c_gt[r] == 0:                     # Aa genotype
            d_allele = bernoulli.rvs(0.5)
            if d_allele == 0:
                d_allele = 'A'
            else:
                d_allele = 'a'
        else:                                  # aa genotype
            d_allele = 'a'
        # Now, we construct genotypes.
        #
        # This mating produces only 'aa' genotypes.
        if s_allele == 'a' and d_allele == 'a':
            # The recessive is lethal.
            if rv['lethal'] == 1:
                calf[6] = 'D'            # The calf is dead
                calf[7] = 'R'            # Because of a recessive lethal
                calf[8] = generation     # In utero
            # In either case (lethal or non-lethal) the genotype is the same.
            calf['genotypes'].append(-1)
        # This mating produces only 'AA' genotype.
        elif s_allele == 'A' and d_allele == 'A':
            # But, oh noes, spontaneous mutation can ruin all teh DNA!!!
            # I put this in to try and keep the lethals from disappearing
            # from the population too quickly. That's why genotypes only
            # change from AA to Aa.
            if random.randint(1, 100001) == 1:
                if debug:
                    print '\t[create_new_calf]: A mutation in recessive %s (%s) happened when ' \
                        'bull %s was mated to cow %s to produce animal %s!' % (r, rk, sire[0],
                                                                               dam[0], calf_id)
                calf['genotypes'].append(0)
            else:
                calf['genotypes'].append(1)
        # These matings can produce only "Aa" genotypes.
        else:
            calf['genotypes'].append(0)

        # Does the calf die before 1 year of age?
        if bernoulli.rvs(calf_loss):
#MM            if debug:
#MM                print '\t[create_new_calf]: Calf %s born to bull %s and cow %s died ' \
#MM                      'before reaching 1 year of age!' % (calf_id, sire[0], dam[0])
            calf['alive'] = 'D'  # The calf is dead
            calf['reason'] = 'C'  # Because of calfhood disease, etc.
            calf['died'] = generation  # During Year 1 of life
            return calf
        else:
            pass

        # Only "dehorn" live calves
        if calf['alive'] == 'A':
        # Do we know about horned status in this scenario?
            if 'Horned' in recessives.keys():
                # Where in the recessives list is the allele we want?
                horned_loc = recessives.keys().index('Horned')
                # Is the calf horned? If so, dehorn them. Polled animals shouldn't die during
                # dehorning. A recessive value of "-1" indicates an aa (horned) genotype.
                if calf['genotypes'][horned_loc] == -1:
                    if bernoulli.rvs(dehorning_loss):
    #MM                    if debug:
    #MM                        print '\t[create_new_calf]: Calf %s was culled due to dehorning complications' % calf[0]
                        calf['alive'] = 'D'  # The calf is dead
                        calf['reason'] = 'H'  # Because of dehorning complications
                        calf['died'] = generation  # In the current generation
                        return calf
                    else:
                        pass
        else:
            pass

        # Only "harvest" live male calves
        if calf['alive'] == 'A':
            # Does the calf get harvested for meat at ~1 year of age?
            if sex == 'M':
                if bernoulli.rvs(harvest_beef):
#MM                    if debug:
#MM                        print '\t[create_new_calf]: Calf %s born to bull %s and cow %s was harvested for Beef at ~1 yr of age ' \
#MM                        % (calf_id, sire[0], dam[0])
                    calf['alive'] = 'D'  # The calf is dead
                    calf['reason'] = 'B'  # Harvested for BEEF
                    calf['died'] = generation  # During Year 1 of life
            else:
                pass
        else:
            pass

        # Update edit status record
        calf['edit_status'].append(0)

        # Set edit and embryo counters to 0
        calf['edit_count'].append(0)
        calf['et_count'].append(0)

    return calf


# This routine performs gene editing, which consists of setting all '1' alleles
# to '0' alleles for the genes to be edited. The process can be unsuccessful if
# edit_fail > 0.


def edit_genes(cows, bulls, dead_cows, dead_bulls, recessives, generation, edit_prop=0.0,
               edit_type='C', edit_trials=1, embryo_trials=1, edit_sex='M', debug=False, extras=()):

    """Edit genes by setting all '1' alleles to '0' alleles for the genes to be edited.

    :param cows: A list of live cow records.
    :type cows: list
    :param bulls: A list of live bull records.
    :type bulls: list
    :param dead_cows: A list of dead cow records.
    :type dead_cows: list
    :param dead_bulls: A list of dead bull records.
    :type dead_bulls: list
    :param recessives: A dictionary of recessives in the population.
    :type recessives: dictionary
    :param generation: The current generation in the simulation.
    :type generation: int
    :param edit_prop: The proportion of animals to edit based on TBV (e.g., 0.01 = 1 %).
    :type edit_prop: list
    :param edit_type: Tool used to edit genes: 'Z' = ZFN, 'T' = TALEN, 'C' = CRISPR, 'P' = no errors.
    :type edit_type: char
    :param edit_trials: The number of attempts to edit an embryo successfully (-1 = repeat until success).
    :type edit_trials: int
    :param embryo_trials: The number of attempts to transfer an edited embryo successfully (-1 = repeat until success).
    :type embryo_trials: int
    :param edit_sex: The sex of animals to be edited (M = males, F = females).
    :type edit_sex: char
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :return: Lists of live and dead animals.
    :rtype: list
    """

    if debug:
        print '\t[edit_genes]: Parameters = '
        print '\t[edit_genes]:     edit_prop = %s' % edit_prop
        print '\t[edit_genes]:     edit_type = %s' % edit_type
        print '\t[edit_genes]:     edit_sex  = %s' % edit_sex
        print '\t[edit_genes]:     %s animals in cows list' % len(cows)
        print '\t[edit_genes]:     %s animals in bulls list' % len(bulls)
        print '\t[edit_genes]:     %s animals in dead_cows list' % len(dead_cows)
        print '\t[edit_genes]:     %s animals in dead_bulls list' % len(dead_bulls)

    # Setup dictionaries of editing technology properties.
    #
    # ZFN and TALEN data taken from: Lillico, S.G., C. Proudfoot, D.F. Carlson,
    #     D. Stverakova, C. Neil, C. Blain, T.J. King, W.A. Ritchie, W. Tan,
    #     A.J. Mileham, D.G. McLaren, S.C. Fahrenkrug, and C.B.A. Whitelaw. 2013. Live
    #     pigs produced from genome edited zygotes. Scientific Reports 3:2847.
    #     doi:10.1038/srep02847.
    #
    # CRISPR/Cas9 data based on: Hai, T., F. Teng, R. Guo, W. Li, and Q. Zhou. 2014.
    #     One-step generation of knockout pigs by zygote injection of CRISPR/Cas system.
    #     Cell Research 24:372.
    #
    # CRISPR ET death rates are from paragraph 5 of Hai et al. (2014). For ZFN/TALEN ET death rates
    # see piglet birth rates from Table 1 in Lillico et al. (2013).
    # death_rate = {'Z': 0.92, 'T': 0.88, 'C': 0.79, 'P': 0.0}
    death_rate = {'A': {'Z': 0.92, 'T': 0.88, 'C': 0.79, 'P': 0.0},
                  'D': {'Z': 0.46, 'T': 0.44, 'C': 0.39, 'P': 0.0},
                  'O': {'Z': 0.92, 'T': 0.88, 'C': 0.79, 'P': 0.0},}
    # CRISPR editing failure rates are from paragraph 5 of Hai et al. (2014). For ZFN/TALEN editing failure rates
    # see "Edited (% of born)" from Table 1 in Lillico et al. (2013).
    # fail_rate = {'Z': 0.89, 'T': 0.79, 'C': 0.37, 'P': 0.0}
    fail_rate = {'A': {'Z': 0.89, 'T': 0.79, 'C': 0.37, 'P': 0.0},
                 'D': {'Z': 0.45, 'T': 0.40, 'C': 0.19, 'P': 0.0},
                 'O': {'Z': 0.89, 'T': 0.79, 'C': 0.37, 'P': 0.0},}

    # Sanity checks on inputs
    if edit_prop < 0.0 or edit_prop > 1.0:
        print '\t[edit_genes]: edit_prop is out of range, %s, which should be [0.0, 1.0]. Using 0.01 ' \
              'instead.' % edit_prop[p]
        edit_prop = 0.01
    if edit_type not in ['Z','T','C', 'P']:
        print '\t[edit_genes]: edit_type has a value of %s, but should be Z (zinc finger nuclease), ' \
              'T (transcription activator-like effector nuclease), C (clustered regularly ' \
              'interspaced short palindromic repeat), or P (perfect, never fails). Using C instead.' % edit_type
        edit_type = 'C'
    if not isinstance(edit_trials, types.IntType):
        print '\t[edit_genes]: edit_trials has a value of %s, but it should be an integer. Using 1 instead.'
        edit_trials = 1
    if edit_trials == 0:
        print '\t[edit_genes]: edit_trials cannot be 0. Using 1 instead.'
        edit_trials = 1
    if not isinstance(embryo_trials, types.IntType):
        print '\t[edit_genes]: embryo_trials has a value of %s, but it should be an integer. Using 1 instead.'
        embryo_trials = 1
    if embryo_trials == 0:
        print '\t[edit_genes]: embryo_trials cannot be 0. Using 1 instead.'
        embryo_trials = 1
    if edit_sex not in ['M','F']:
        print '\t[edit_genes]: edit_sex has a value of %s, but should be M male) or F (Female). ' \
              'Using F instead.' % edit_sex
        edit_sex = 'F'

    if edit_sex == 'M':
        animals = bulls
        dead_animals = dead_bulls
    else:
        animals = cows
        dead_animals = dead_cows

    # We don't want this down in the animal loop because it's slow to keep
    # calling it over and over again.
    next_id = get_next_id(cows, bulls, dead_cows, dead_bulls, *extras)

    # Do the actual gene editing. Here's how that works.
    #     0. Sort the animals on TBV
    # For each recessive to be edited:
    #     1. Select the top edit_prop proportion of animals, at least 1 animal always will be edited
    #     2. Do the edit for Aa and aa genotypes
    #     3. Check to see if the edit succeeded
    #     4. Update the animal's genotype
    #     5. Update the edit_status list
    # Once the recessives are processed:
    #     6. Sort the list on animal ID in ascending order
    #     7. Return the list
    #
    n_edit = int(round(len(animals) * edit_prop, 1))
    if n_edit < 1:
        n_edit = 0
        print '\t[edit_genes]: Zero of %s animals were edited with edit_prop = %s and edit_type = %s.' %\
              (len(animals), edit_prop, edit_type)

    # 07/01/2016: What a mess. Note that in the original code (copied below) the ET success check was done after each
    # recessive was edited, which is obviously incorrect. It's an artifact of that code being written to loop over
    # recessives, rather than over animals. It is the embryo (animal) that is the ultimate "unit" here since the
    # embryo lives or dies. So, I had to rewrite the loops to loop over recessives WITHIN animals, rather than the
    # other way around. Happy Canada Day!

    # If we have animals to edit, check and see if we have recessives to edit.
    if n_edit > 0:
        # 0. Sort the animals on TBV
        animals.sort(key=lambda x: x['tbv'], reverse=True)
        # 1. Select the top edit_prop proportion of animals.
        for animal in range(n_edit):
            # Create a "clone" of the animal to be edited, which could represent
            # a zygote created by SCNT.
            ed_animal = copy.deepcopy(animals[animal])
            # Give the animal a new ID
            #next_id = get_next_id(cows, bulls, dead_cows, dead_bulls)
            # print "[edit_genes]: Next ID for the %s-th animal to be edited is %s." % (animal, next_id)
            ed_animal['animal'] = next_id
            # Update the birth year
            ed_animal['born'] = generation
            # For each recessive:
            for rk, rv in recessives.iteritems():
                r = recessives.keys().index(rk)
                # 2. Do the edit for Aa and aa genotypes, where
                #    1 is an AA, 0 is an Aa, and a -1 is aa.
                if ed_animal['genotypes'][r] in [0, -1]:
                    # 3. Check to see if the edit succeeded.
                    # 3a. First, was the embryo successfully edited?
                    # 3a. (i) if edit_trials > 0 then only a fixed number of trials
                    #         will be carried out. If there is no success before the
                    #         final trial then the editing process fails.
                    if edit_trials > 0:
                        outcomes = bernoulli.rvs(1.-fail_rate[rv['edit_mode']][edit_type], size=edit_trials)
                        if outcomes.any():
                                # 4. Update the animal's genotype
                                ed_animal['genotypes'][r] = 1
                                # 5. Update the edit_status list
                                ed_animal['edit_status'][r] = 1
                                # 6. Update the animal's edit count with the time of the first successful edit
                                ed_animal['edit_count'][r] = np.min(np.nonzero(outcomes)) + 1
                                break
                    # 3a. (ii) if edit_trials < 0 then then the editing process will be
                    #          repeated until a success occurs.
                    elif edit_trials < 0:
                        edit_count = 0
                        while True:
                            edit_count += 1
                            if bernoulli.rvs(1.-fail_rate[rv['edit_mode']][edit_type]):
                                # 4. Update the animal's genotype
                                ed_animal['genotypes'][r] = 1
                                # 5. Update the edit_status list
                                ed_animal['edit_status'][r] = 1
                                # 6. Update the animal's edit count
                                ed_animal['edit_count'][r] = edit_count
                                break
                    #  3a. (iii) edit_trials should never be zero because of the sanity checks, but catch it just in
                    #            case. You know users are...
                    else:
                        print "[edit_genes]: edit_trials should never be 0, skipping editing step!"
            # 3b. Was the edited embryo successfully carried to term?
            if embryo_trials > 0:
                # 3b. (i) If the embryo died then we need to update the cause and time of death,
                #         and move it to the dead animals list. If edit_trials > 0 then only a fixed number of trials
                #         will be carried out. If there is no success before the final trial then the editing process
                #         fails.
                outcomes = bernoulli.rvs(1. - death_rate[rv['edit_mode']][edit_type], size=embryo_trials)
                if not outcomes.any():
                    ed_animal['alive'] = 'D'                # The animal is dead
                    ed_animal['reason'] = 'G'                # Because of gene editing
                    ed_animal['died'] = generation         # In the current generation
                    if edit_sex == "M":
                        dead_bulls.append(ed_animal)    # Add it to the dead animals list
                    else:
                        dead_cows.append(ed_animal)
                else:
                    # 6. Update the animal's ET count
                    ed_animal['et_count'] = np.min(np.nonzero(outcomes)) + 1
            # 3b. (ii) If the embryo died then we need to update the cause and time of death,
            #          and move it to the dead animals list. If edit_trials < 0 then then the editing process will
            #          be repeated until a success occurs. The ET operation never adds a dead embryo to the dead
            #          animals list, failures just increment the number-of-attempts counter.
            elif embryo_trials < 0:
                embryo_count = 0
                while True:
                    embryo_count += 1
                    if bernoulli.rvs(1. - death_rate[rv['edit_mode']][edit_type]):
                        # 6. Update the animal's ET count
                        ed_animal['et_count'] = embryo_count
                        break
            # 3b. (iii) embryo_trials should never be zero because of the sanity checks, but catch it just in
            #           case. You know users are...
            else:
                print "[edit_genes]: embryo_trials should never be 0, skipping ET step!"

            # Now we have to remove the dead animals from the live animals list
            # animals[:] = [a for a in animals if a[6] == 'A']

            # Add the new, edited animal to the population
            ed_animal['reason'] = 'G'          # Animal created by gene editing
            ed_animal['died'] = generation     # In the current generation
            if edit_sex == "M":
                bulls.append(ed_animal)
            else:
                cows.append(ed_animal)

            next_id += 1

            # if debug:
            #     print "\t[edit_genes]: Edited ID:   %s" % ed_animal
            #     print "\t[edit_genes]: Original ID: %s" % animals[animal]

            # if debug:
            #     if edit_sex == 'M':
            #         print "[edit_gene]: A new, edited bull, %s, was added to the bulls list!" % \
            #             ed_animal[0]
            #     else:
            #         print "[edit_gene]: A new, edited cow, %s, was added to the cows list!" % \
            #             ed_animal[0]

    # 6. Sort the list on animal ID in ascending order
    animals.sort(key=lambda x: x['animal'])

    print '\t[edit_genes]:     %s animals in cows list' % len(cows)
    print '\t[edit_genes]:     %s animals in bulls list' % len(bulls)
    print '\t[edit_genes]:     %s animals in dead_cows list' % len(dead_cows)
    print '\t[edit_genes]:     %s animals in dead_bulls list' % len(dead_bulls)

    # 7. Return the lists
    return cows, bulls, dead_cows, dead_bulls


def cull_bulls(bulls, dead_bulls, generation, max_bulls=250, max_bull_age=3, debug=False):

    """Cull excess and old bulls from the population.

    :param bulls: A list of live bull records.
    :type bulls: list
    :param dead_bulls: A list of records of dead bulls.
    :type dead_bulls: list
    :param generation: The current generation in the simulation.
    :type generation: int
    :param max_bulls: The maximum number of bulls that can be alive at one time.
    :type max_bulls: int
    :param max_bull_age: The maximum age for bulls before culling.
    :type max_bull_age: int
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :return: Lists of live and dead bulls.
    :rtype: list
    """

    if debug:
        print '[cull_bulls]: live bulls: %s' % len(bulls)
        print '[cull_bulls]: dead bulls: %s' % len(dead_bulls)
    if max_bulls <= 0:
        print "[cull_bulls]: max_bulls cannot be <= 0! Setting to 250."
        max_bulls = 250
    if debug:
        print "[cull_bulls]: Computing age distribution."
        age_distn(bulls, generation)
    # This is the age cull
    n_culled = 0
    for b in bulls:
        if (generation - b['born']) > max_bull_age:
            b['alive'] = 'D'            # This bull is dead
            b['reason'] = 'A'            # From age
            b['died'] = generation     # In the current generation
            dead_bulls.append(b)  # Add it to the dead bulls list
            n_culled += 1
    if debug:
        print '\t[cull_bulls]: %s bulls culled for age in generation %s (age>%s)' % \
              (n_culled, generation, max_bull_age)
    # Now we have to remove the dead bulls from the bulls list
    bulls[:] = [b for b in bulls if b['alive'] == 'A']
    # Check to see if we need to cull on number (count).
    if len(bulls) <= max_bulls:
        if debug:
            print '\t[cull_bulls]: No bulls culled in generation %s (bulls<max_bulls)' % generation
        return bulls, dead_bulls
    # If this culling is necessary then we need to update the records of the
    # culled bulls and move them into the dead_bulls list. We cull bulls on
    # TBV.
    else:
        # Now we're going to sort on TBV in ascending order
        bulls.sort(key=lambda x: x['tbv'])
        n_culled = 0
        for b in bulls[0:len(bulls)-max_bulls]:
            b['alive'] = 'D'           # This bull is dead
            b['reason'] = 'N'           # Because there were too many of them
            b['died'] = generation    # In the current generation
            dead_bulls.append(b)
            n_culled += 1
        bulls = bulls[len(bulls)-max_bulls:]
        if debug:
            print '\t[cull_bulls]: %s bulls culled because of excess population in generation %s ' \
                  '(bulls>max_bulls)' % (n_culled, generation)
        return bulls, dead_bulls


# Return a list of unique herd IDs from a list of animal records.


def get_unique_herd_list(animals, debug=False):

    """Return a list of unique herd IDs from a list of animal records.

    :param animals: A list of animal records.
    :type animals: list
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :return: List of unique herd IDs.
    :rtype: list

    """

    herd_list = []
    for a in animals:
        if a['herd'] not in herd_list:
            herd_list.append(a['herd'])
    if debug:
        print '\t\t[get_unique_herd_list]: %s unique herds found' % len(herd_list)
    return herd_list


# Function for moving bulls from nucleus herds to multiplier herds.
#
# 03/28/2019    JBC    Fixed reference to old index for herd ID missed when updating animals to dictionaries.

def move_nucleus_bulls_to_multiplier(bulls, nucleus_bulls, generation, nucleus_bulls_to_move, bull_criterion,
                                     move_rule='random', move_age=1, debug=True):

    """Move bulls from nucleus herds to multiplier herds.

    :param bulls: A list of live bull records.
    :type bulls: list
    :param nucleus_bulls: A list of live nucleus bull records.
    :type nucleus_bulls: list
    :param generation: The current generation in the simulation.
    :type generation: int
    :param nucleus_bulls_to_move: The total number of bulls to move from nucleus to multiplier herds.
    :type nucleus_bulls_to_move: int
    :param bull_criterion: Decision criterion for moving bulls. (MM, ??)
    :type bull_criterion: string
    :param move_rule: The strategy used to move bulls ('random').
    :type move_rule: string
    :param move_age: Age cut-off for moving from nucleus to multipliers.
    :type move_age: int
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :return: Lists of live multiplier and nucleus herd bulls.
    :rtype: list

    """

    if debug:
        print '\t[move_nucleus_bulls_to_multiplier]: Preparing to move %s bulls from nucleus to multiplier herds.' % \
            nucleus_bulls_to_move

    herd_list = get_unique_herd_list(bulls, debug)
    herd_list_queue = collections.deque(herd_list)
    herd_list_queue2 = collections.deque(herd_list)
    nucleus_herd_list = get_unique_herd_list(nucleus_bulls, debug)

    # Get list of 1 year old nucleus bulls to be moved
    move_list = []
    for nb in nucleus_bulls:
        # move_age is 1 so we only move yearlings that will be ready to breed next gen as 2 yr olds.
        if generation - nb['born'] == move_age:
            move_list.append(nb)

    # Are there enough yearling nucleus bulls?
    if debug:
        if  len(move_list) < nucleus_bulls_to_move:
            print '\t[move_nucleus_bulls_to_multiplier]: Fewer nucleus herd yearling bulls (%s) available than needed for ' \
            'multiplier herds (%s)!' % (len(move_list), nucleus_bulls_to_move)
        else:
            print '\t[move_nucleus_bulls_to_multiplier]: More nucleus herd yearling bulls (%s) available than needed for ' \
            'multiplier herds (%s)!' % (len(move_list), nucleus_bulls_to_move)
    # How many bulls should we keep in the nucleus herd (ex: 3%)
    to_keep = int( math.floor(0.05*len(move_list)) )
    # Need to keep at least 1 bull for nucleus matings
    if to_keep == 0:
        to_keep = 1
    if debug:
        print '\t[move_nucleus_bulls_to_multiplier]: Keeping at least %s nucleus bulls for nucleus matings.' % \
              to_keep
    # How many total yearling bulls are left to be moved (including the remainder), while keeping top % for nucleus?
    to_move = int( (len(move_list)-to_keep) )
    # can't move a negative number of bulls
    if to_move < 0:
        to_move = 0
    # How many bulls will be evenly moved into each herd? (We can't move fractional bulls, so this may result in
    # fewer bulls than desired being moved if the number of bulls isn't an even multiple of the number of herds.
    move_per_herd = int( math.floor(float(to_move) / float(len(herd_list))) )
    # The total number of bulls that can evenly be moved to mult. herds
    even_move = (move_per_herd*len(herd_list))
    if to_move == 0:
        remainder = 0
    else:
        remainder = to_move - even_move

    if debug:
        print '\t[move_nucleus_bulls_to_multiplier]: (%s) nucleus bulls will be moved per multiplier herd & (%s) will also ' \
                 'randomly be moved, for a total of (%s) nucleus bulls moved' % (move_per_herd, remainder, to_move)
    if bull_criterion == 'polled':
        # Sort 1 year old bulls on recessives 1st (1,0,-1) and 2nd by TBV in ascending (smallest to largest) order
        move_list.sort(key=lambda x: (x['genotypes'],x['tbv']), reverse=False)
        # Trim list so we only move as many bulls as allows us to keep a top % of bulls back for nucleus matings
        move_list = move_list[0:to_move]

    # bull_criterion is 'random'
    else:
        # Sort 1 year old bulls only on TBV in ascending (smallest to largest) order
        move_list.sort(key=lambda x: x['tbv'], reverse=False)
        # Trim list so we only move as many bulls as allows us to keep a top % of bulls back for nucleus matings
        move_list = move_list[0:to_move]
    # now shuffle the left-over bulls to be moved to mult. herds
    # For now, randomly assign nucleus bulls to multiplier herds. I think that there coud be other schemes that
    # might make sense, too, but I'm not 100% sure yet.
    #if move_rule == 'random':
##    print(move_list)
    random.shuffle(move_list)

    if len(move_list) > to_move:
        if debug:
            print '\t[move_nucleus_bulls_to_multiplier]: move_list contains more bulls (%s) than needed for ' \
                'multiplier herds (%s), trimming' % ( len(move_list), to_move )
        move_list = move_list[0:even_move]

    # Loop over the list of nucleus herd bulls and change the herd IDs for the ones that are moving.
    # 1st move the bulls by mvoing move_per_herd # to each herd
    for i in xrange(0,even_move):
        if i % move_per_herd == 0:
            nh = herd_list_queue.pop()
#MM        if debug:
#MM            print '\t\t[move_nucleus_bulls_to_multiplier]: Evenly moving bull %s from nucleus herd %s to multiplier ' \
#MM                'herd %s' % (move_list[i][0], move_list[i][5], nh)
        move_list[i]['herd'] = nh
    # 2nd move the remainder bulls 1x1 randomly to herds
    for i in xrange(even_move, to_move):
        nh = herd_list_queue2.pop()
#MM        if debug:
#MM            print '\t\t[move_nucleus_bulls_to_multiplier]: Moving remainder bull %s from nucleus herd %s to multiplier ' \
#MM                'herd %s' % (move_list[i][0], move_list[i][5], nh)
        move_list[i]['herd'] = nh

    # Once the herd IDs have been changed we need to actually change the lists of nucleus and multiplier bulls
    # so that what we return reflects the animal movements.s3ppa1a
    if debug:
        print '[move_nucleus_bulls_to_multiplier]: bulls contains %s animals before moving nucleus bulls' % len(bulls)
    for ml in move_list:
        bulls.append(ml)
    if debug:
        print '[move_nucleus_bulls_to_multiplier]: bulls contains %s animals after moving nucleus bulls' % len(bulls)

    if debug:
        print '[move_nucleus_bulls_to_multiplier]: nucleus_bulls contains %s animals before removing bulls' %\
              len(nucleus_bulls)
    nucleus_bulls[:] = [b for b in nucleus_bulls if b not in move_list]
    if debug:
        print '[move_nucleus_bulls_to_multiplier]: nucleus_bulls contains %s animals after removing bulls' %\
              len(nucleus_bulls)

    return bulls, nucleus_bulls


# Print a table showing how many animals of each age are in the population. Returns a
# dictionary of results. If the "show" parameter is True then print the table to
# the console.


def age_distn(animals, generation, show=True):

    """Print a table showing how many animals of each age are in the population.

    :param animals: A list of live animal records.
    :type animals: list
    :param generation: The current generation in the simulation.
    :type generation: int
    :param show: Boolean. Activate/deactivate printing of the age distribution.
    :type show: bool
    :return: Lists of live and dead cows.
    :rtype: dictionary
    """

    ages = {}
    for a in animals:
        age = generation - a['born']
        if age not in ages.keys():
            ages[age] = 0
        ages[age] += 1
    if show:
        keys = ages.keys()
        keys.sort()
        print '\tAnimal age distribution'
        for k in keys:
            print '\t%s:\t\t%s' % (k, ages[k])
    return ages


# This routine culls cows each generation. The rules used are:
# 1.  Cows cannot be more than 5 years old
# 2.  There is an [optional] involuntary cull at a user-specified rate 
# 3.  After that, cows are culled at random to get down to the maximum herd size
# 4.  Cows may be culled due to complications during the dehorning process (beef scenario)


def cull_cows(cows, dead_cows, generation, recessives, base_herds, max_cows=0, max_cow_age=10, culling_rate=0.0,
              debug=False):

    """Cull excess and old cows from the population.

    :param cows: A list of live cow records.
    :type cows: list
    :param dead_cows: A list of records of dead cows.
    :type dead_cows: list
    :param generation: The current generation in the simulation.
    :type generation: int
    :param max_cows: The maximum number of cows that can be alive at one time.
    :type max_cows: int
    :param max_cow_age: The maximum age for cow before culling.
    :type max_cow_age: int
    :param culling_rate: The proportion of cows culled involuntarily each generation.
    :type culling_rate: float
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :return: Lists of live and dead cows.
    :rtype: list
    """

    if debug:
        print '[cull_cows]: live cows: %s' % len(cows)
        print '[cull_cows]: dead cows: %s' % len(dead_cows)
    # 0 means keep all cows after age-related and involuntary culling
    if max_cows < 0:
        print "[cull_cows]: max_cows cannot be < 0! Setting to 0."
        max_cows = 0
    if debug:
        print "[cull_cows]: Computing age distribution."
        age_distn(cows, generation)

    # This is the age cull
    n_culled = 0
    for c in cows:
        if (generation - c['born']) > max_cow_age:
            c['alive'] = 'D'            # This cow is dead
            c['reason'] = 'A'            # Because of her age
            c['died'] = generation     # In the current generation
            dead_cows.append(c)   # Add it to the dead cows list
            n_culled += 1
    if debug: print '\t[cull_cows]: %s cows culled for age in generation %s (age>%s)' % \
                    (n_culled, generation, max_cow_age)
    # Now we have to remove the dead animals from the cows list
    cows[:] = [c for c in cows if c['alive'] == 'A']
    # Now for the involuntary culling!
    if culling_rate > 0:
        n_culled = 0
        for c in cows:
            if random.uniform(0, 1) < culling_rate:
                c['alive'] = 'D'                          # This cow is dead
                c['reason'] = 'I'                          # Because of involuntary culling
                c['died'] = generation                   # In the current generation
                dead_cows.append(copy.copy(c))      # Add it to the dead cows list
                n_culled += 1
        if debug:
            print '\t[cull_cows]: %s cows involuntarily culled in generation %s' % (n_culled, generation)
    # Now we have to remove the dead animals from the cows list
    cows[:] = [c for c in cows if c['alive'] == 'A']
    # Now we're going to sort on TBV in ascending order
    #cows.sort(key=lambda x: x[9])
    # Instead of culling from only the low tail, we'll cull at random.
    random.shuffle(cows)
    # Check to see if we need to cull on number (count).
    if max_cows == 0:
        if debug:
            print '\t[cull_cows]: No cows were culled to maintain herd size in generation %s (max_cows=0)' % generation
        return cows, dead_cows
    elif len(cows) < max_cows:
        early_max_cows = int( math.floor((float(len(cows)) / base_herds)) )
        if debug:
            print '\t[cull_cows]: (n < max_cows), but might cull inidividual herds because early_max_cows to maintain even herd sizes is (%s) in generation %s.' \
            % (early_max_cows, generation)
        c_diff = len(cows) - max_cows
        if c_diff < 0:
            c_diff = 0
        max_herd_size = early_max_cows
        if debug:
            print '[cull_cows]: There are %s extra cows total and the max herd size is %s in generation %s' % (c_diff, max_herd_size, generation)
        cull_herd = {}
        for herd in xrange(base_herds):
            cull_herd[herd] = []
            herd_cows = [c for c in cows if c['herd'] == herd]
            herd_diff = int(len(herd_cows) - max_herd_size)
            if len(herd_cows) > max_herd_size:
                cull_herd[herd] = herd_cows[0:herd_diff]
                for c in cows:
                    if c in cull_herd[herd]:
                        c['alive'] = 'D'                          # This cow is dead
                        c['reason'] = 'N'                          # Because there were too many of them
                        c['died'] = generation                   # In the current generation
                        dead_cows.append(copy.copy(c))      # Add it to the dead cows list
##                if debug:
##                    print '\t[cull_cows]: %s cows were culled from herd (%s) to maintain herd size (cows > early_max_cows)' % (len(cull_herd[herd]), herd)
##            else:
##                if debug:
##                    print '\t[cull_cows]: No cows were culled from herd (%s), which only has %s cows.' % (herd, len(herd_cows))
    # len(cows) is > max_cows so culling is definitely necessary
    else:
        c_diff = len(cows) - max_cows
        max_herd_size = int(math.floor(max_cows/base_herds))
        if debug:
            print '[cull_cows]: There are %s extra cows total and the max herd size is %s in generation %s' % (c_diff, max_herd_size, generation)
        cull_herd = {}
        for herd in xrange(base_herds):
            cull_herd[herd] = []
            herd_cows = [c for c in cows if c['herd'] == herd]
            herd_diff = int(len(herd_cows) - max_herd_size)
            if len(herd_cows) > max_herd_size:
                cull_herd[herd] = herd_cows[0:herd_diff]
                for c in cows:
                    if c in cull_herd[herd]:
                        c['alive'] = 'D'                          # This cow is dead
                        c['reason'] = 'N'                          # Because there were too many of them
                        c['died'] = generation                   # In the current generation
                        dead_cows.append(copy.copy(c))      # Add it to the dead cows list
##                if debug:
##                    print '\t[cull_cows]: %s cows were culled from herd (%s) to maintain herd size (cows > max_cows)' % (len(cull_herd[herd]), herd)
            else:
                if debug:
                    print '\t[cull_cows]: No cows were culled from herd (%s), which only has %s cows.' % (herd, len(herd_cows))

    # Now we have to remove the dead animals from the cows list
    cows[:] = [c for c in cows if c['alive'] == 'A']

    return cows, dead_cows


# Compute simple summary statistics of TBV for the list of animals passed in:
#    sample mean
#    min, max, and count
#    sample variance and standard deviation


def animal_summary(animals):

    """Compute simple summary statistics of TBV for the list of animals passed in.

    :param animals: A list of live animal records.
    :type animals: list
    :return: Sample size, minimum, maximum, mean, variance, and standard deviation.
    :rtype: float
    """

    total = 0.
    count = 0.
    tmin = float('inf')
    tmax = float('-inf')
    sumx = 0.
    sumsq = 0.
    for a in animals:
        count += 1
        total = total + a['tbv']
        if a['tbv'] < tmin:
            tmin = a['tbv']
        if a['tbv'] > tmax:
            tmax = a['tbv']
        sumx = sumx + a['tbv']
        sumsq += a['tbv']**2
    if count == 0.:
        samplemean = -999.
        samplevar = -999.
        samplestd = -999.
    elif count == 1.:
        samplemean = total / count
        samplevar = -999.
        samplestd = -999.
    else:
        samplemean = total / count
        samplevar = (1 / (count-1)) * (sumsq - (sumx**2 / count))
        samplestd = math.sqrt(samplevar)
    return count, tmin, tmax, samplemean, samplevar, samplestd


# The easy way to determine the current MAF for each recessive is to count
# the number of copies of each "a" allele in the current population of live
# animals.
#
# cows              : A list of live cow records
# bulls             : A list of live bull records
# generation        : The current generation in the simulation
# recessives        : A Python list of recessives in the population
# freq_hist         : A dictionary of minor allele frequencies for each generation
# show_recessives   : When True, print summary information for each recessive.


def update_maf(cows, bulls, generation, recessives, freq_hist, show_recessives=False):

    """Determine minor allele freuencies for each recessive by allele counting.

    :param cows: A list of live cow records.
    :type cows: list
    :param bulls: A list of live bull records.
    :type bulls: list
    :param generation: The current generation in the simulation.
    :type generation: int
    :param recessives: A dictionary of recessives in the population.
    :type recessives: dictionary
    :param freq_hist: Minor allele frequencies for each generation.
    :type freq_hist: dictionary
    :param show_recessives: Boolean. Print summary information for each recessive.
    :type show_recessives: bool
    :return: List of recessives with updated frequencies and dictionary of frequencies.
    :rtype: list and dictionary
    """

    minor_allele_counts = []
    for rk in recessives.keys():
        minor_allele_counts.append(0)
    # Loop over the bulls list and count
    for b in bulls:
        for r in xrange(len(recessives.keys())):
            # A genotype code of 0 is a heterozygote (Aa), and a 1 is a homozygote (AA)
            if b['genotypes'][r] == 0:
                minor_allele_counts[r] += 1
            # As of 06/02/2014 homozygous recessives aren't necessarily lethal, so
            # we need to make sure that we count them, too.
            if b['genotypes'][r] == -1:
                minor_allele_counts[r] += 2
    # Loop over the cows list and count
    for c in cows:
        for r in xrange(len(recessives.keys())):
            # A genotype code of 0 is a heterozygote (Aa), and a 1 is a homozygote (AA)
            if c['genotypes'][r] == 0:
                minor_allele_counts[r] += 1
            # As of 06/02/2014 homozygous recessives aren't necessarily lethal, so
            # we need to make sure that we count them, too.
            if c['genotypes'][r] == -1:
                minor_allele_counts[r] += 2
    # Now we have to calculate the MAF for each recessive
    total_alleles = 2 * (len(cows) + len(bulls))
    freq_hist[generation] = []
    for rk, rv in recessives.iteritems():
        r = recessives.keys().index(rk)
        # r_freq is the frequency of the minor allele (a)
        r_freq = float(minor_allele_counts[r]) / float(total_alleles)
        # Is the recessive lethal? Yes?
        if rv['lethal'] == 1:
            # Compute the frequency of the AA and Aa genotypes
            denom = (1. - r_freq)**2 + (2 * r_freq * (1. - r_freq))
            f_dom = (1. - r_freq)**2 / denom
            f_het = (2 * r_freq * (1. - r_freq)) / denom
            if show_recessives:
                print
                print '\tRecessive %s (%s), generation %s:' % (r, rk, generation)
                print '\t\tminor alleles = %s\t\ttotal alleles = %s' % (minor_allele_counts[r], total_alleles)
                print '\t\tp = %s\t\tq = %s' % ((1. - r_freq), r_freq)
                print '\t\t  = %s\t\t  = %s' % ((1. - r_freq) - (1. - rv['frequency']),
                                                r_freq - rv['frequency'])
                print '\t\tf(AA) = %s\t\tf(Aa) = %s' % (f_dom, f_het)
        # Well, okay, so it's not.
        else:
            # Compute the frequency of the AA and Aa genotypes
            f_dom = (1. - r_freq)**2
            f_het = (2 * r_freq * (1. - r_freq))
            f_rec = r_freq**2
            if show_recessives:
                print
                print '\tThis recessive is ***NOT LETHAL***'
                print '\tRecessive %s (%s), generation %s:' % (r, rk, generation)
                print '\t\tminor alleles = %s\t\ttotal alleles = %s' % (minor_allele_counts[r], total_alleles)
                print '\t\tp = %s\t\tq = %s' % ((1. - r_freq), r_freq)
                print '\t\t  = %s\t\t  = %s' % ((1. - r_freq) - (1. - rv['frequency']),
                                                r_freq - rv['frequency'])
                print '\t\tf(AA) = %s\t\tf(Aa) = %s' % (f_dom, f_het)
                print '\t\tf(aa) = %s' % f_rec
        # Finally, update the recessives and history tables
        recessives[rk]['frequency'] = r_freq
        freq_hist[generation].append(r_freq)
    return recessives, freq_hist


def disposal_reasons(dead_bulls, dead_cows):

    """Produce a table showing the reasons that animals died.

\   :param dead_cows: A list of dead cow records.
    :type dead_cows: list
    :param dead_bulls: A list of dead bull records.
    :type dead_bulls: list
    :return: Nothing is returned from this function.
    :rtype: None
    """

    df = pd.DataFrame.from_dict(dead_bulls+dead_cows, orient='columns')
    print df.groupby(['died', 'reason']).count()['animal']

    return

def bred_count(cows):

    """Produce a table showing the number of cows bred or left open per generation.

\   :param dead_cows: A list of live cow records.
    :type dead_cows: list
    :return: Nothing is returned from this function.
    :rtype: None
    """

    df = pd.DataFrame.from_dict(cows, orient='columns')
    print df.groupby(['bred', 'born']).count()['animal']

    return

# Write file conaining simunlation parameters.
#
# 03/08/2019    JBC    Created function.

def write_parameter_file(scenario='random', cow_mean=0., genetic_sd=200., bull_diff=1.5, polled_diff=[1.0,1.3],
                 gens=20, percent=0.10, base_bulls=500, base_max_bull_age=5, base_cows=2500, base_max_cow_age=5,
                 service_bulls=50, base_herds=100, bull_mating_age=1, cow_mating_age=1, max_bulls=1500, max_bull_age=5,
                 max_cows=7500, max_cow_age=10, debug=False, filetag='', recessives={}, max_matings=500, rng_seed=None,
                 show_recessives=False, history_freq='end', edit_prop=[0.0,0.0], edit_type='C', edit_trials=1,
                 embryo_trials=1, embryo_inbreeding=False, flambda=25., bull_criterion='polled', bull_deficit='horned',
                 base_polled='homo', carrier_penalty=False, bull_copies=4, polled_parms=[0.0,0.0,0.0],
                 bull_unique=False, calf_loss=0.0, nucleus_calf_loss=0.0, dehorning_loss=0.0,
                 nucleus_dehorning_loss=0.0, harvest_beef=0.0, nucleus_harvest_beef=0.0, culling_rate=0.0,
                 check_all_parms=True, show_disposals=True,
                 use_nucleus=False, nucleus_cow_mean=200., nucleus_genetic_sd=200., nucleus_bull_diff=1.5,
                 nucleus_base_bulls=100, nucleus_base_max_bull_age=10, nucleus_base_cows=5000,
                 nucleus_base_max_cow_age=10, nucleus_base_herds=10, nucleus_service_bulls=15, nucleus_max_bulls=750,
                 nucleus_max_bull_age=10, nucleus_max_cows=10000, nucleus_max_cow_age=10,
                 nucleus_max_matings=5000, nucleus_bulls_to_move=500, nucleus_filetag='',
                 nucleus_recessives={},):

    """Write file containing simulation parameters.

    :param scenario: The mating strategy to use in the current scenario ('random'|'trunc'|'pryce').
    :type scenario: string
    :param cow_mean: Average base population cow TBV.
    :type cow_mean: float
    :param genetic_sd: Additive genetic SD of the simulated trait.
    :type genetic_sd: float
    :param bull_diff: Differential between base cows and bulls, in genetic SD.
    :type bull_diff: float
    :param polled_diff: Difference between Pp and pp bulls, and PP and pp bulls, in genetic SD.
    :type polled_diff: List of floats
    :param gens: Total number of generations to run the simulation.
    :type gens: int
    :param percent: Percent of bulls to use as sires in the truncation mating scenario.
    :type percent: float
    :param base_bulls: The number of bulls in the base population.
    :type base_bulls: int
    :param base_max_bull_age: Maximum age of base population bulls
    :type base_max_bull_age: int
    :param max_bull_age: Maximum age of bulls
    :type max_bull_age: int
    :param base_cows: The number of cows in the base population.
    :type base_cows: int
    :param base_max_cow_age: Maximum age of base population cows
    :type base_max_cow_age: int
    :param max_cow_age: Maximum age of cows
    :type max_cow_age: int
    :param service_bulls: The number of herd bulls to use in each herd each generation.
    :type service_bulls: int
    :param base_herds: The number of herds in the population.
    :type base_herds: int
    :param bull_mating_age: The minimum age for bulls to breed cows.
    :type generation: int
    :param cow_mating_age: The minimum age for cows to be bred and have a calf in the same generation.
    :type generation: int
    :param max_bulls: The maximum number of bulls that can be alive at one time.
    :type max_bulls: int
    :param max_cows: The maximum number of cows that can be alive at one time.
    :type max_cows: int
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :param filetag: Added to file names to describe the analysis a file is associated with.
    :type filetag: string
    :param recessives: Dictionary of recessive alleles in the population.
    :type recessives: dictionary
    :param max_matings: The maximum number of matings permitted for each bull.
    :type max_matings: int
    :param show_recessives: Boolean. Print summary information for each recessive.
    :type show_recessives: bool
    :param history_freq: When 'end', save only files from final generation, else save every generation.
    :type history_freq: string
    :param edit_prop: The proportion of animals to edit based on TBV (e.g., 0.01 = 1 %).
    :type edit_prop: list
    :param edit_type: Tool used to edit genes: 'Z' = ZFN, 'T' = TALEN, 'C' = CRISPR, 'P' = no errors.
    :type edit_type: char
    :param edit_trials: The number of attempts to edit an embryo successfully (-1 = repeat until success).
    :type edit_trials: int
    :param embryo_trials: The number of attempts to transfer an edited embryo successfully (-1 = repeat until success).
    :type embryo_trials: int
    :param embryo_inbreeding: Write a file of coefficients of inbreeding for all possible bull-by-cow matings.
    :type embryo_inbreeding: boolean
    :param flambda: Decrease in economic merit (in US dollars) per 1% increase in inbreeding.
    :type flambda: float
    :param bull_criterion: Criterion used to select the group of bulls for mating.
    :type bull_criterion: string
    :param bull_deficit: Manner of handling too few bulls for matings: 'use_horned' or 'no_limit'.
    :type bull_deficit: string
    :param base_polled: Genotype of polled animals in the base population ('homo'|'het'|'both')
    :type base_polled: string
    :param carrier_penalty: Penalize carriers for carrying a copy of an undesirable allele (True), or not (False)
    :rtype carrier_penalty: bool
    :param bull_copies: Genotype of polled bulls selected for mating (0|1|2|4|5|6)
    :type bull_copies: integer
    :param polled_parms: Proportion of polled bulls, proportion of PP, and proportion of Pp bulls.
    :type polled_parms: list of floats
    :param calf_loss: Proportion of calves that die before they reach 1 year of age.
    :type calf_loss: float
    :param dehorning_loss: The proportion of cows that die during dehorning.
    :type dehorning_loss: float
    :param culling_rate: The proportion of cows culled involuntarily each generation.
    :type culling_rate: float
    :param check_all_parms: Perform a formal check on all parameters.
    :type check_all_parms: bool
    :param show_disposals: Print a summary of disposal reasons following the history_freq flag.
    :type show_disposals: bool
    :param use_nucleus: Create and use nucleus herds to propagate elite genetics
    :type use_nucleus: bool
    :param nucleus_cow_mean: Average nucleus cow TBV.
    :type nucleus_cow_mean: float
    :param nucleus_genetic_sd: Additive genetic SD of the simulated trait.
    :type nucleus_genetic_sd: float
    :param nucleus_bull_diff: Differential between nucleus cows and bulls, in genetic SD
    :type nucleus_bull_diff: float
    :param nucleus_base_bulls: Initial number of bulls in nucleus herds
    :type nucleus_base_bulls: int
    :param nucleus_base_cows: Initial number of cows in nucleus herds
    :type nucleus_base_cows: int
    :param nucleus_base_herds: Number of nucleus herds in the population
    :type nucleus_base_herds: int
    :param nucleus_service_bulls: Number of bulls to use in each nucleus herd each generation.
    :type nucleus_service_bulls: int
    :param nucleus_max_bulls: Maximum number of live bulls to keep each generation in nucleus herds
    :type nucleus_max_bulls: int
    :param nucleus_max_cows: Maximum number of live cows to keep each generation in nucleus herds
    :type nucleus_max_cows: int
    :param nucleus_max_matings: The maximum number of matings permitted for each nucleus herd bull
    :type nucleus_max_matings: int
    :param nucleus_bulls_to_move: The number of bulls to move from nucleus herds to the multiplier herds each year
    :type nucleus_bulls_to_move: int
    :param nucleus_filetag: Added to file names to describe the analysis a nucleus herd file is associated with.
    :type nucleus_filetag: string
    :param nucleus_base_max_bull_age: Maximum age of base population bulls in nucleus herds
    :type nucleus_base_max_bull_age: int
    :param nucleus_base_max_cow_age: Maximum age of base population cows in nucleus herds
    :type nucleus_base_max_cow_age: int
    :param nucleus_recessives: Dictionary of recessive alleles in the nucleus population.
    :type nucleus_recessives: dictionary
    :return: Nothing is returned from this function.
    :rtype: None
    """

    try:
        # Save the simulation parameters so that we know what we did.
        outfile = 'simulation_parameters%s.txt' % filetag
        ofh = file(outfile, 'w')
        ofh.write('scenario                     :\t%s\n' % scenario)
        ofh.write('filetag                      :\t%s\n' % filetag)
        ofh.write('cow_mean                     :\t%s\n' % cow_mean)
        ofh.write('genetic_sd                   :\t%s\n' % genetic_sd)
        ofh.write('bull_diff                    :\t%s\n' % bull_diff)
        ofh.write('polled_diff:\n')
        ofh.write('                                     %s\n' % polled_diff[0])
        ofh.write('                                     %s\n' % polled_diff[1])
        ofh.write('polled_parms:\n')
        ofh.write('        percent polled               :\t%s\n' % polled_parms[0])
        ofh.write('        percent PP                   :\t:%s\n' % polled_parms[1])
        ofh.write('        percent Pp                   :\t%s\n' % polled_parms[2])
        ofh.write('percent                      :\t%s\n' % percent)
        ofh.write('base bulls                   :\t%s\n' % base_bulls)
        ofh.write('base max bull age            :\t%s\n' % base_max_bull_age)
        ofh.write('max bull age                 :\t%s\n' % max_bull_age)
        ofh.write('base bulls                   :\t%s\n' % base_bulls)
        ofh.write('base cows                    :\t%s\n' % base_cows)
        ofh.write('base max cow age             :\t%s\n' % base_max_cow_age)
        ofh.write('max cow  age                 :\t%s\n' % max_cow_age)
        ofh.write('base herds                   :\t%s\n' % base_herds)
        ofh.write('base polled                  :\t%s\n' % base_polled)
        ofh.write('service bulls                :\t%s\n' % service_bulls)
        ofh.write('max bulls                    :\t%s\n' % max_bulls)
        ofh.write('bull_mating_age              :\t%s\n' % bull_mating_age)
        ofh.write('max cows                     :\t%s\n' % max_cows)
        ofh.write('cow_mating_age               :\t%s\n' % cow_mating_age)
        ofh.write('edit_prop (cows)             :\t%s\n' % edit_prop[1])
        ofh.write('edit_prop (bulls)            :\t%s\n' % edit_prop[0])
        ofh.write('edit_type                    :\t%s\n' % edit_type)
        ofh.write('edit_trials                  :\t%s\n' % edit_trials)
        ofh.write('embryo_trials                :\t%s\n' % embryo_trials)
        ofh.write('embryo_inbreeding            :\t%s\n' % embryo_inbreeding)
        ofh.write('flambda                      :\t%s\n' % flambda)
        ofh.write('show_recessives              :\t%s\n' % show_recessives)
        ofh.write('Recessives in multiplier herds\n')
        for rk, rv in recessives.iteritems():
            r = recessives.keys().index(rk)
            ofh.write('Base MAF  %s                     :\t%s\n' % (r + 1, rv['frequency']))
            ofh.write('Cost      %s                     :\t%s\n' % (r + 1, rv['value']))
            ofh.write('Lethal    %s                     :\t%s\n' % (r + 1, rv['lethal']))
            ofh.write('Name      %s                     :\t%s\n' % (r + 1, rk))
            ofh.write('Edit      %s                     :\t%s\n' % (r + 1, rv['edit']))
            ofh.write('Edit mode %s                     :\t%s\n' % (r + 1, rv['edit_mode']))
        ofh.write('max_matings                  :\t%s\n' % max_matings)
        ofh.write('Debug                        :\t%s\n' % debug)
        ofh.write('Filetag                      :\t%s\n' % filetag)
        ofh.write('RNG seed                     :\t%s\n' % rng_seed)
        ofh.write('history_freq                 :\t%s\n' % history_freq)
        ofh.write('bull_deficit                 :\t%s\n' % bull_deficit)
        ofh.write('bull_criterion               :\t%s\n' % bull_criterion)
        ofh.write('carrier_penalty              :\t%s\n' % carrier_penalty)
        ofh.write('bull_copies                  :\t%s\n' % bull_copies)
        ofh.write('bull_unique                  :\t%s\n' % bull_unique)
        ofh.write('calf_loss                    :\t%s\n' % calf_loss)
        ofh.write('nucleus_calf_loss            :\t%s\n' % nucleus_calf_loss)
        ofh.write('dehorning_loss               :\t%s\n' % dehorning_loss)
        ofh.write('nucleus_dehorning_loss       :\t%s\n' % nucleus_dehorning_loss)
        ofh.write('harvest_beef                 :\t%s\n' % harvest_beef)
        ofh.write('nucleus_harvest_beef         :\t%s\n' % nucleus_harvest_beef)
        ofh.write('culling_rate                 :\t%s\n' % culling_rate)
        ofh.write('check_all_parms              :\t%s\n' % check_all_parms)
        ofh.write('show_disposals               :\t%s\n' % show_disposals)
        ofh.write('use_nucleus                  :\t%s\n' % use_nucleus)
        ofh.write('nucleus_cow_mean             :\t%s\n' % nucleus_cow_mean)
        ofh.write('nucleus_genetic_sd           :\t%s\n' % nucleus_genetic_sd)
        ofh.write('nucleus_bull_diff            :\t%s\n' % nucleus_bull_diff)
        ofh.write('nucleus_base_bulls           :\t%s\n' % nucleus_base_bulls)
        ofh.write('nucleus_base_max_bull_age    :\t%s\n' % nucleus_base_max_bull_age)
        ofh.write('nucleus_base_cows            :\t%s\n' % nucleus_base_cows)
        ofh.write('nucleus_base_max_cow_age     :\t%s\n' % nucleus_base_max_cow_age)
        ofh.write('nucleus_base_herds           :\t%s\n' % nucleus_base_herds)
        ofh.write('nucleus_service_bulls        :\t%s\n' % nucleus_service_bulls)
        ofh.write('nucleus_max_bulls            :\t%s\n' % nucleus_max_bulls)
        ofh.write('nucleus_max_bull_age         :\t%s\n' % nucleus_max_bull_age)
        ofh.write('nucleus_max_cows             :\t%s\n' % nucleus_max_cows)
        ofh.write('nucleus_max_cow_age          :\t%s\n' % nucleus_max_cow_age)
        ofh.write('nucleus_max_matings          :\t%s\n' % nucleus_max_matings)
        ofh.write('nucleus_bulls_to_move        :\t%s\n' % nucleus_bulls_to_move)
        ofh.write('nucleus_filetag              :\t%s\n' % nucleus_filetag)
        ofh.write('Recessives in nucleus herds\n')
        for rk, rv in nucleus_recessives.iteritems():
            r = nucleus_recessives.keys().index(rk)
            ofh.write('Base MAF  %s                     :\t%s\n' % (r + 1, rv['frequency']))
            ofh.write('Cost      %s                     :\t%s\n' % (r + 1, rv['value']))
            ofh.write('Lethal    %s                     :\t%s\n' % (r + 1, rv['lethal']))
            ofh.write('Name      %s                     :\t%s\n' % (r + 1, rk))
            ofh.write('Edit      %s                     :\t%s\n' % (r + 1, rv['edit']))
            ofh.write('Edit mode %s                     :\t%s\n' % (r + 1, rv['edit_mode']))
        ofh.close()
        return True
    except:
        return False


# We're going to go ahead and write files containing various pieces
# of information from the simulation.
def write_history_files(cows, bulls, dead_cows, dead_bulls, generation, filetag=''):

    """Write output files, including animal records, simulation parameters, and recessive information.

    :param cows: A list of live cow records.
    :type cows: list
    :param bulls: A list of live bull records.
    :type bulls: list
    :param dead_cows: A list of dead cow records.
    :type dead_cows: list
    :param dead_bulls: A list of dead bull records.
    :type dead_bulls: list
    :param generation: The current generation in the simulation.
    :type generation: int
    :param filetag: Added to file names to describe the analysis a file is associated with.
    :type filetag: string
    :return: Nothing is returned from this function.
    :rtype: None
    """

    # First, write the animal history files.
    cowfile = 'cows_history%s_%s.txt' % (filetag, generation)
    deadcowfile = 'dead_cows_history%s_%s.txt' % (filetag, generation)
    bullfile = 'bulls_history%s_%s.txt' % (filetag, generation)
    deadbullfile = 'dead_bulls_history%s_%s.txt' % (filetag, generation)
    # Column labels
    headerline = 'animal\tsire\tdam\tborn\tsex\therd\tstatus\tcause\tdied\tTBV\tinbreeding\tedited\tn edits\tn ETs\tbred\trecessives\n'
    # Keys for specifying order of columns in output
    key_order = ['animal', 'sire', 'dam', 'born', 'sex', 'herd', 'alive', 'reason', 'died', 'tbv',
                 'inbreeding', 'edit_status', 'edit_count', 'et_count', 'bred', 'genotypes']
    # Cows
    ofh = file(cowfile, 'w')
    ofh.write(headerline)
    for c in cows:
        outline = ''
        for k in key_order:
            if len(outline) == 0:
                outline += '%s' % c[k]
            else:
                outline += '\t%s' % c[k]
        outline += '\n'
        ofh.write(outline)
    ofh.close()
    # Dead cows
    ofh = file(deadcowfile, 'w')
    ofh.write(headerline)
    for c in dead_cows:
        outline = ''
        for k in key_order:
            if len(outline) == 0:
                outline += '%s' % c[k]
            else:
                outline += '\t%s' % c[k]
        outline += '\n'
        ofh.write(outline)
    ofh.close()
    # Bulls
    ofh = file(bullfile, 'w')
    ofh.write(headerline)
    for b in bulls:
        outline = ''
        for k in key_order:
            if len(outline) == 0:
                outline += '%s' % b[k]
            else:
                outline += '\t%s' % b[k]
        outline += '\n'
        ofh.write(outline)
    ofh.close()
    # Dead bulls
    ofh = file(deadbullfile, 'w')
    ofh.write(headerline)
    for b in dead_bulls:
        outline = ''
        for k in key_order:
            if len(outline) == 0:
                outline += '%s' % b[k]
            else:
                outline += '\t%s' % b[k]
        outline += '\n'
        ofh.write(outline)
    ofh.close()


# If the file could have the generation, bred cow count and open cow count that would be great. One file for
# multiplier cows and one for the nucleus would be perfect.

def write_repro_history_files(cows, generation, filetag=''):

    """Produce a table showing the reasons that animals died.

    :param cows: A list of live cow records.
    :type cows: list
    :param generation: The current generation in the simulation.
    :type generation: int
    :param filetag: Added to file names to describe the analysis a file is associated with.
    :type filetag: string
    :return: Nothing is returned from this function.
    :rtype: None
    """

    df = pd.DataFrame.from_dict(cows, orient='columns')
    df['generation'] = generation
    df['cows_bred'] = np.where(df['bred'] == 1, 1, 0)
    df['cows_open'] = np.where(df['bred'] == 0, 1, 0)
    try:
        outfilename = 'cow_breeding_history%s.txt' % ( filetag )
        # If we're in the first generation, create a new history file that will be appended to by later generations.
        if generation == 1:
            outfilemode = 'w'
            df.groupby(['generation']).sum().reset_index().to_csv(outfilename,
                                                                  sep='\t',
                                                                  columns=['generation', 'cows_bred', 'cows_open'],
                                                                  index=False,
                                                                  mode=outfilemode)
        else:
            outfilemode = 'a'
            df.groupby(['generation']).sum().reset_index().to_csv(outfilename,
                                                                  sep='\t',
                                                                  columns=['generation', 'cows_bred', 'cows_open'],
                                                                  index=False,
                                                                  header=False,
                                                                  mode=outfilemode)
        return True
    except:
        return False


# If the file could have the generation, bred cow count and open cow count that would be great. One file for
# multiplier cows and one for the nucleus would be perfect.
#
# 03/15/2019    JBC     Created.
# 03/28/2019    JBC     Fixed output to be correct format.


def write_death_reasons_history_file(dead_cows, dead_bulls, filetag=''):

    """Produce a table showing the reasons that animals died.

    :param dead_cows: A list of dead cow records.
    :type dead_cows: list
    :param dead_bulls: A list of dead bull records.
    :type dead_bulls: list
    :param filetag: Added to file names to describe the analysis a file is associated with.
    :type filetag: string
    :return: True on sucess, false on failure.
    :rtype: boolean
    """

    df = pd.DataFrame.from_dict(dead_cows + dead_bulls, orient='columns')
    #df.groupby(['sex', 'died', 'reason']).count()['animal'].reset_index()
    new_df = df.groupby(['sex', 'died', 'reason']).size().reset_index(name='counts')
    #print new_df

    try:
        outfilename = 'death_reasons_history%s.txt' % ( filetag )
        outfilemode = 'w'
        new_df.to_csv(outfilename,
            sep='\t',
            columns=['sex', 'died', 'reason', 'counts'],
            index=False,
            mode=outfilemode)
        return True
    except:
        return False


# Write file of allele frequencies.
#
# 03/08/2019    JBC    Created function.


def write_allele_freqs(freq_hist, nucleus_freq_hist={}, filetag='', nucleus_filetag=''):

    """Write file of allele frequencies.

        :param freq_hist: A dictionary of allele frequencies by generation.
        :type freq_hist: dictionary
        :param nucleus_freq_hist: A dictionary of nucleus herd allele frequencies by generation.
        :type nucleus_freq_hist: dictionary
        :param filetag: Added to file names to describe the analysis a file is associated with.
        :type filetag: string
        :param nucleus_filetag: Added to file names to describe the analysis a file is associated with.
        :type nucleus_filetag: string
        :return: Nothing is returned from this function.
        :rtype: None
        """

    # Save the allele frequency history
    outfile = 'minor_allele_frequencies%s.txt' % filetag
    ofh = file(outfile, 'w')
    for k, v in freq_hist.iteritems():
        outline = '%s' % k
        for frequency in v:
            outline += '\t%s' % frequency
        outline += '\n'
        ofh.write(outline)
    ofh.close()

    if len(nucleus_freq_hist)>0:
        outfile = 'minor_allele_nucleus_frequencies%s.txt' % nucleus_filetag
        ofh = file(outfile, 'w')
        for k, v in nucleus_freq_hist.iteritems():
            outline = '%s' % k
            for frequency in v:
                outline += '\t%s' % frequency
            outline += '\n'
            ofh.write(outline)
        ofh.close()


# Main loop for individual simulation scenarios.
#
# 02/26/2019    JBC    Added 'base_max_bull_age', 'max_bull_age', 'base_max_cow_age',
#                      'max_cow_age', fixed 'nucleus_recessives' checking.


def run_scenario(scenario='random', cow_mean=0., genetic_sd=200., bull_diff=1.5, polled_diff=[1.0,1.3],
                 gens=20, percent=0.10, base_bulls=500, base_max_bull_age=5, base_cows=2500, base_max_cow_age=5,
                 service_bulls=50, base_herds=100, bull_mating_age=1, cow_mating_age=1, max_bulls=1500, max_bull_age=5,
                 max_cows=7500, max_cow_age=10, debug=False, filetag='', recessives={}, max_matings=500, rng_seed=None,
                 show_recessives=False, history_freq='end', edit_prop=[0.0,0.0], edit_type='C', edit_trials=1,
                 embryo_trials=1, embryo_inbreeding=False, flambda=25., bull_criterion='polled', bull_deficit='horned',
                 base_polled='homo', carrier_penalty=False, bull_copies=4, polled_parms=[0.0,0.0,0.0],
                 bull_unique=False, calf_loss=0.0, nucleus_calf_loss=0.0, dehorning_loss=0.0,
                 nucleus_dehorning_loss=0.0, harvest_beef=0.0,
                 nucleus_harvest_beef=0.0, culling_rate=0.0, check_all_parms=True, show_disposals=True,
                 use_nucleus=False, nucleus_cow_mean=200., nucleus_genetic_sd=200., nucleus_bull_diff=1.5,
                 nucleus_base_bulls=100, nucleus_base_max_bull_age=10, nucleus_base_cows=5000,
                 nucleus_base_max_cow_age=10, nucleus_base_herds=10, nucleus_service_bulls=15, nucleus_max_bulls=750,
                 nucleus_max_bull_age=10, nucleus_max_cows=10000, nucleus_max_cow_age=10,
                 nucleus_max_matings=5000, nucleus_bulls_to_move=500, nucleus_filetag='',
                 nucleus_recessives={},
                ):

    """Main loop for individual simulation scenarios.

    :param scenario: The mating strategy to use in the current scenario ('random'|'trunc'|'pryce').
    :type scenario: string
    :param cow_mean: Average base population cow TBV.
    :type cow_mean: float
    :param genetic_sd: Additive genetic SD of the simulated trait.
    :type genetic_sd: float
    :param bull_diff: Differential between base cows and bulls, in genetic SD.
    :type bull_diff: float
    :param polled_diff: Difference between Pp and pp bulls, and PP and pp bulls, in genetic SD.
    :type polled_diff: List of floats
    :param gens: Total number of generations to run the simulation.
    :type gens: int
    :param percent: Percent of bulls to use as sires in the truncation mating scenario.
    :type percent: float
    :base_bulls: The number of bulls in the base population.
    :type base_bulls: int
    :param base_max_bull_age: Maximum age of base population bulls
    :type base_max_bull_age: int
    :param max_bull_age: Maximum age of bulls
    :type max_bull_age: int
    :param base_cows: The number of cows in the base population.
    :type base_cows: int
    :param base_max_cow_age: Maximum age of base population cows
    :type base_max_cow_age: int
    :param max_cow_age: Maximum age of cows
    :type max_cow_age: int
    :param service_bulls: The number of herd bulls to use in each herd each generation.
    :type service_bulls: int
    :param base_herds: The number of herds in the population.
    :type base_herds: int
    :param bull_mating_age: The minimum age for bulls to breed cows.
    :type generation: int
    :param cow_mating_age: The minimum age for cows to be bred and have a calf in the same generation.
    :type generation: int
    :param max_bulls: The maximum number of bulls that can be alive at one time.
    :type max_bulls: int
    :param max_cows: The maximum number of cows that can be alive at one time.
    :type max_cows: int
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :param filetag: Added to file names to describe the analysis a file is associated with.
    :type filetag: string
    :param recessives: Dictionary of recessive alleles in the population.
    :type recessives: dictionary
    :param max_matings: The maximum number of matings permitted for each bull.
    :type max_matings: int
    :param show_recessives: Boolean. Print summary information for each recessive.
    :type show_recessives: bool
    :param history_freq: When 'end', save only files from final generation, else save every generation.
    :type history_freq: string
    :param edit_prop: The proportion of animals to edit based on TBV (e.g., 0.01 = 1 %).
    :type edit_prop: list
    :param edit_type: Tool used to edit genes: 'Z' = ZFN, 'T' = TALEN, 'C' = CRISPR, 'P' = no errors.
    :type edit_type: char
    :param edit_trials: The number of attempts to edit an embryo successfully (-1 = repeat until success).
    :type edit_trials: int
    :param embryo_trials: The number of attempts to transfer an edited embryo successfully (-1 = repeat until success).
    :type embryo_trials: int
    :param embryo_inbreeding: Write a file of coefficients of inbreeding for all possible bull-by-cow matings.
    :type embryo_inbreeding: boolean
    :param flambda: Decrease in economic merit (in US dollars) per 1% increase in inbreeding.
    :type flambda: float
    :param bull_criterion: Criterion used to select the group of bulls for mating.
    :type bull_criterion: string
    :param bull_deficit: Manner of handling too few bulls for matings: 'use_horned' or 'no_limit'.
    :type bull_deficit: string
    :param base_polled: Genotype of polled animals in the base population ('homo'|'het'|'both')
    :type base_polled: string
    :param carrier_penalty: Penalize carriers for carrying a copy of an undesirable allele (True), or not (False)
    :rtype carrier_penalty: bool
    :param bull_copies: Genotype of polled bulls selected for mating (0|1|2|4|5|6)
    :type bull_copies: integer
    :param polled_parms: Proportion of polled bulls, proportion of PP, and proportion of Pp bulls.
    :type polled_parms: list of floats
    :param calf_loss: Proportion of calves that die before they reach 1 year of age.
    :type calf_loss: float
    :param dehorning_loss: The proportion of cows that die during dehorning.
    :type dehorning_loss: float
    :param culling_rate: The proportion of cows culled involuntarily each generation.
    :type culling_rate: float
    :param check_all_parms: Perform a formal check on all parameters.
    :type check_all_parms: bool
    :param show_disposals: Print a summary of disposal reasons following the history_freq flag.
    :type show_disposals: bool
    :param use_nucleus: Create and use nucleus herds to propagate elite genetics
    :type use_nucleus: bool
    :param nucleus_cow_mean: Average nucleus cow TBV.
    :type nucleus_cow_mean: float
    :param nucleus_genetic_sd: Additive genetic SD of the simulated trait.
    :type nucleus_genetic_sd: float
    :param nucleus_bull_diff: Differential between nucleus cows and bulls, in genetic SD
    :type nucleus_bull_diff: float
    :param nucleus_base_bulls: Initial number of bulls in nucleus herds
    :type nucleus_base_bulls: int
    :param nucleus_base_cows: Initial number of cows in nucleus herds
    :type nucleus_base_cows: int
    :param nucleus_base_herds: Number of nucleus herds in the population
    :type nucleus_base_herds: int
    :param nucleus_service_bulls: Number of bulls to use in each nucleus herd each generation.
    :type nucleus_service_bulls: int
    :param nucleus_max_bulls: Maximum number of live bulls to keep each generation in nucleus herds
    :type nucleus_max_bulls: int
    :param nucleus_max_cows: Maximum number of live cows to keep each generation in nucleus herds
    :type nucleus_max_cows: int
    :param nucleus_max_matings: The maximum number of matings permitted for each nucleus herd bull
    :type nucleus_max_matings: int
    :param nucleus_bulls_to_move: The number of bulls to move from nucleus herds to the multiplier herds each year
    :type nucleus_bulls_to_move: int
    :param nucleus_filetag: Added to file names to describe the analysis a nucleus herd file is associated with.
    :type nucleus_filetag: string
    :param nucleus_base_max_bull_age: Maximum age of base population bulls in nucleus herds
    :type nucleus_base_max_bull_age: int
    :param nucleus_base_max_cow_age: Maximum age of base population cows in nucleus herds
    :type nucleus_base_max_cow_age: int
    :param nucleus_recessives: Dictionary of recessive alleles in the nucleus population.
    :type nucleus_recessives: dictionary
    :return: Nothing is returned from this function.
    :rtype: None
    """

    # Initialize the PRNG.
    random.seed(rng_seed)

    # Before we do anything else, check the parameters
    if check_all_parms:
        check_result = check_parameters(scenario, cow_mean, genetic_sd, bull_diff, polled_diff, gens, percent, base_bulls,
                        base_max_bull_age, max_bull_age, base_cows, base_max_cow_age, max_cow_age,
                        service_bulls, base_herds, bull_mating_age, cow_mating_age, max_bulls, max_cows, debug, filetag,
                        recessives, max_matings, rng_seed, show_recessives, history_freq, edit_prop,
                        edit_type, edit_trials, embryo_trials, embryo_inbreeding, flambda, bull_criterion,
                        bull_deficit, base_polled, carrier_penalty, bull_copies, polled_parms, bull_unique,
                        calf_loss, nucleus_calf_loss, dehorning_loss, nucleus_dehorning_loss, harvest_beef, nucleus_harvest_beef,
                        culling_rate, show_disposals, use_nucleus,
                        nucleus_cow_mean, nucleus_genetic_sd, nucleus_bull_diff, nucleus_base_bulls, nucleus_base_max_bull_age,
                        nucleus_base_cows, nucleus_base_max_cow_age, nucleus_base_herds, nucleus_service_bulls, nucleus_max_bulls,
                        nucleus_max_bull_age, nucleus_max_cows, nucleus_max_cow_age, nucleus_max_matings, nucleus_bulls_to_move,
                        nucleus_filetag, nucleus_recessives)

        if not check_result:
            print '[run_scenario]: Errors in simulation parameters, cannot continue, stopping program.'
            sys.exit(0)

    # This is the initial setup
    # !!! The normal (non-nucleus) herds must be created before the nucleus herds in order to avoid
    # !!! overlapping IDs.
    print '[run_scenario]: Setting-up the simulation at %s' % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cows, bulls, dead_cows, dead_bulls, freq_hist = create_base_population(
        cow_mean,
        genetic_sd=genetic_sd,
        bull_diff=bull_diff,
        polled_diff=polled_diff,
        base_bulls=base_bulls,
        base_max_bull_age=base_max_bull_age,
        base_cows=base_cows,
        base_max_cow_age=base_max_cow_age,
        base_herds=base_herds,
        force_carriers=True,
        force_best=True,
        recessives=recessives,
        check_tbv=False,
        rng_seed=rng_seed,
        base_polled=base_polled,
        polled_parms=polled_parms,
        use_nucleus=False,
        debug=debug,
    )

    # This is the set-up for nucleus herds
    # !!! The regular (non-nucleus) base population MUST be created before the base nucleus herds in order
    # !!! to avoid ID duplication.
    if use_nucleus:
        print '[run_scenario]: Setting-up nucleus herds at %s' % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls, nucleus_freq_hist = create_base_population(
            nucleus_cow_mean,
            genetic_sd=nucleus_genetic_sd,
            bull_diff=nucleus_bull_diff,
            polled_diff=polled_diff,
            base_bulls=nucleus_base_bulls,
            base_max_bull_age=nucleus_base_max_bull_age,
            base_cows=nucleus_base_cows,
            base_max_cow_age=nucleus_base_max_cow_age,
            base_herds=nucleus_base_herds,
            force_carriers=True,
            force_best=True,
            recessives=nucleus_recessives,
            check_tbv=False,
            rng_seed=rng_seed,
            base_polled=base_polled,
            polled_parms=polled_parms,
            use_nucleus=True,
            debug=debug,
            extras=(cows, bulls, dead_cows, dead_bulls),
        )

        if debug:
            print 'Next available animal ID after nucleus population created:\t\t%s' %\
                  get_next_id(cows, bulls, dead_cows, dead_bulls, nucleus_cows, nucleus_bulls, nucleus_dead_cows,
                              nucleus_dead_bulls)

    # We need empty lists even if no nucleus herds are being used to make the non-duplicate-ID code work. Yes, it's
    # a bad hack, I wouldn't do it this way from scratch...
    else:
        nucleus_cows = []
        nucleus_bulls = []
        nucleus_dead_cows = []
        nucleus_dead_bulls = []

    # Get the MAF for each founder generation
    for g in xrange(-9,1,1):
        recessives, freq_hist = update_maf(cows, bulls, g, recessives, freq_hist, show_recessives)
        if use_nucleus:
            nucleus_recessives, nucleus_freq_hist = update_maf(nucleus_cows, nucleus_bulls, g, nucleus_recessives,
                                                               nucleus_freq_hist, show_recessives)
        #else:
        #    nucleus_recessives, nucleus_freq_hist = {}, {}

    # This is the start of the next generation
    for generation in xrange(1, gens+1):
        print '\n[run_scenario]: Beginning generation %s at %s' % (
            generation, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

        # First, mate the animals, which creates new offspring
        print
        print '\tGeneration %s' % generation
        print '\t\t              \tLive\tLive \tLive \tDead\tDead \tDead'
        print '\t\t              \tCows\tBulls\tTotal\tCows\tBulls\tTotal'
        print '\t\tBefore mating:\t%s\t%s\t%s\t%s\t%s\t%s' % \
              (len(cows), len(bulls), len(cows)+len(bulls),
               len(dead_cows), len(dead_bulls),
               len(dead_cows)+len(dead_bulls))

        # If we're using nucleus herds, mate them, too
        if use_nucleus:
            print
            print '\tNucleus Herds Generation %s' % generation
            print '\t\t              \tLive\tLive \tLive \tDead\tDead \tDead'
            print '\t\t              \tNucl\tNucl \tNucl \tNucl\tNucl \tNucl'
            print '\t\t              \tCows\tBulls\tTotal\tCows\tBulls\tTotal'
            print '\t\tBefore mating:\t%s\t%s\t%s\t%s\t%s\t%s' % \
                  (len(nucleus_cows), len(nucleus_bulls), len(nucleus_cows) + len(nucleus_bulls),
                   len(nucleus_dead_cows), len(nucleus_dead_bulls),
                   len(nucleus_dead_cows) + len(nucleus_dead_bulls))

        # This is the code that handles the mating scenarios

        # Animals are mated at random with an [optional] limit on the number of matings
        # allowed to each bull.
        if scenario == 'random':
            print '\n[run_scenario]: Mating cows randomly at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            cows, bulls, dead_cows, dead_bulls = random_mating(
                cows,
                bulls,
                dead_cows,
                dead_bulls,
                generation,
                generations,
                recessives,
                max_matings=max_matings,
                edit_prop=edit_prop,
                edit_type=edit_type,
                edit_trials=1,
                embryo_trials=1,
                edit_sex='M',
                calf_loss=calf_loss,
                dehorning_loss=dehorning_loss,
                harvest_beef=harvest_beef,
                debug=debug,
                extras=(nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls),
            )

            if debug:
                print 'Next available animal ID after random mating in gen %s:\t\t%s' % \
                      ( generation, get_next_id(cows, bulls, dead_cows, dead_bulls, nucleus_cows, nucleus_bulls,
                                                nucleus_dead_cows, nucleus_dead_bulls) )

            if use_nucleus:
                print '\n[run_scenario]: Mating nucleus cows randomly at %s' % \
                      datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls = random_mating(
                    nucleus_cows,
                    nucleus_bulls,
                    nucleus_dead_cows,
                    nucleus_dead_bulls,
                    generation,
                    generations,
                    nucleus_recessives,
                    max_matings=nucleus_max_matings,
                    edit_prop=edit_prop,
                    edit_type=edit_type,
                    edit_trials=1,
                    embryo_trials=1,
                    edit_sex='M',
                    calf_loss=calf_loss,
                    dehorning_loss=dehorning_loss,
                    harvest_beef=harvest_beef,
                    debug=debug,
                    extras=(cows, bulls, dead_cows, dead_bulls),
                )

                if debug:
                    print 'Next available animal ID after random nucleus herd mating in gen %s:\t\t%s' % \
                          (generation, get_next_id(cows, bulls, dead_cows, dead_bulls, nucleus_cows, nucleus_bulls,
                                                   nucleus_dead_cows, nucleus_dead_bulls))

        # Only the top "pct" of bulls, based on TBV, are mated randomly to the cow
        # population with no limit on the number of matings allowed. This is a simple
        # example of truncation selection.
        elif scenario == 'truncation':
            print '\n[run_scenario]: Mating cows using truncation selection at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            cows, bulls, dead_cows, dead_bulls = truncation_mating(
                cows,
                bulls,
                dead_cows,
                dead_bulls,
                generation,
                gens,
                recessives,
                pct=percent,
                edit_prop=edit_prop,
                edit_type=edit_type,
                edit_trials=edit_trials,
                embryo_trials=embryo_trials,
                edit_sex='M',
                calf_loss=calf_loss,
                dehorning_loss=dehorning_loss,
                harvest_beef=harvest_beef,
                debug=debug,
                extras=(nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls),
            )

            if debug:
                print 'Next available animal ID after truncation mating in gen %s:\t\t%s' % \
                      ( generation, get_next_id(cows, bulls, dead_cows, dead_bulls, nucleus_cows, nucleus_bulls,
                                                nucleus_dead_cows, nucleus_dead_bulls) )

            if use_nucleus:
                print '\n[run_scenario]: Mating nucleus cows using truncation selection at %s' % \
                      datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls = truncation_mating(
                    nucleus_cows,
                    nucleus_bulls,
                    nucleus_dead_cows,
                    nucleus_dead_bulls,
                    generation,
                    gens,
                    nucleus_recessives,
                    pct=percent,
                    edit_prop=edit_prop,
                    edit_type=edit_type,
                    edit_trials=edit_trials,
                    embryo_trials=embryo_trials,
                    edit_sex='M',
                    calf_loss=calf_loss,
                    dehorning_loss=dehorning_loss,
                    harvest_beef=harvest_beef,
                    debug=debug,
                    extras=(cows, bulls, dead_cows, dead_bulls),
                )

                if debug:
                    print 'Next available animal ID after nucleus truncation mating in gen %s:\t\t%s' % \
                          (generation, get_next_id(cows, bulls, dead_cows, dead_bulls, nucleus_cows, nucleus_bulls,
                                                   nucleus_dead_cows, nucleus_dead_bulls))

        # Bulls are mated to cows using a mate allocation strategy similar to that of
        # Pryce et al. (2012), in which the PA is discounted to account for decreased
        # fitness associated with increased rates of inbreeding. We're not using genomic
        # information in this study but we assume perfect pedigrees, so everything should
        # work out okay.
        elif scenario == 'pryce':
            print '\n[run_scenario]: Mating cows using Pryce\'s method at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            cows, bulls, dead_cows, dead_bulls = pryce_mating(
                cows,
                bulls,
                dead_cows,
                dead_bulls,
                generation,
                gens,
                filetag,
                recessives,
                max_matings=max_matings,
                base_herds=base_herds,
                bull_mating_age=bull_mating_age,
                cow_mating_age=cow_mating_age,
                debug=debug,
                penalty=False,
                service_bulls=service_bulls,
                edit_prop=edit_prop,
                edit_type=edit_type,
                edit_trials=edit_trials,
                embryo_trials=embryo_trials,
                embryo_inbreeding=embryo_inbreeding,
                edit_sex='M',
                flambda=flambda,
                bull_criterion=bull_criterion,
                bull_deficit=bull_deficit,
                carrier_penalty=carrier_penalty,
                bull_copies=bull_copies,
                bull_unique=bull_unique,
                calf_loss=calf_loss,
                dehorning_loss=dehorning_loss,
                harvest_beef=harvest_beef,
                extras=(nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls),
            )

            if debug:
                print 'Next available animal ID after Pryce mating in gen %s:\t\t%s' % \
                      ( generation, get_next_id(cows, bulls, dead_cows, dead_bulls, nucleus_cows, nucleus_bulls,
                                                nucleus_dead_cows, nucleus_dead_bulls) )

            if use_nucleus:
                print '\n[run_scenario]: Mating nucleus cows using Pryce\'s method at %s' % \
                      datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls = pryce_mating(
                    nucleus_cows,
                    nucleus_bulls,
                    nucleus_dead_cows,
                    nucleus_dead_bulls,
                    generation,
                    gens,
                    nucleus_filetag,
                    nucleus_recessives,
                    max_matings=nucleus_max_matings,
                    base_herds=nucleus_base_herds,
                    bull_mating_age=bull_mating_age,
                    cow_mating_age=cow_mating_age,
                    debug=debug,
                    penalty=False,
                    service_bulls=nucleus_service_bulls,
                    edit_prop=edit_prop,
                    edit_type=edit_type,
                    edit_trials=edit_trials,
                    embryo_trials=embryo_trials,
                    embryo_inbreeding=embryo_inbreeding,
                    edit_sex='M',
                    flambda=flambda,
                    bull_criterion=bull_criterion,
                    bull_deficit=bull_deficit,
                    carrier_penalty=carrier_penalty,
                    bull_copies=bull_copies,
                    bull_unique=bull_unique,
                    calf_loss=calf_loss,
                    dehorning_loss=dehorning_loss,
                    harvest_beef=harvest_beef,
                    extras=(cows, bulls, dead_cows, dead_bulls),
                )

                if debug:
                    print 'Next available animal ID after nucleus Pryce mating in gen %s:\t\t%s' % \
                          (generation, get_next_id(cows, bulls, dead_cows, dead_bulls, nucleus_cows, nucleus_bulls,
                                                   nucleus_dead_cows, nucleus_dead_bulls))

        # Bulls are mated to cows using a mate allocation strategy similar to that of
        # Pryce et al. (2012), in which the PA is discounted to account for decreased
        # fitness associated with increased rates of inbreeding. We're not using genomic
        # information in this study but we assume perfect pedigrees, so everything should
        # work out okay. In addition, the PA are adjusted to account for the effects of
        # the recessives carried by the parents.
        elif scenario == 'pryce_r':
            print '\n[run_scenario]: Mating cows using Pryce\'s method and accounting for recessives at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            cows, bulls, dead_cows, dead_bulls = pryce_mating(
                cows,
                bulls,
                dead_cows,
                dead_bulls,
                generation,
                gens,
                filetag,
                recessives,
                max_matings=max_matings,
                base_herds=base_herds,
                bull_mating_age=bull_mating_age,
                cow_mating_age=cow_mating_age,
                debug=debug,
                penalty=True,
                service_bulls=service_bulls,
                edit_prop=edit_prop,
                edit_type=edit_type,
                edit_trials=edit_trials,
                embryo_trials=embryo_trials,
                embryo_inbreeding=embryo_inbreeding,
                edit_sex='M',
                flambda=flambda,
                bull_criterion=bull_criterion,
                bull_deficit=bull_deficit,
                carrier_penalty=carrier_penalty,
                bull_copies=bull_copies,
                bull_unique=bull_unique,
                calf_loss=calf_loss,
                dehorning_loss=dehorning_loss,
                harvest_beef=harvest_beef,
                extras=(nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls),
            )

            if debug:
                print 'Next available animal ID after Pryce + R mating in gen %s:\t\t%s' % \
                      ( generation, get_next_id(cows, bulls, dead_cows, dead_bulls, nucleus_cows, nucleus_bulls,
                                                nucleus_dead_cows, nucleus_dead_bulls) )

            if use_nucleus:
                print '\n[run_scenario]: Mating nucleus cows using Pryce\'s method and accounting for recessives at %s' % \
                      datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls = pryce_mating(
                    nucleus_cows,
                    nucleus_bulls,
                    nucleus_dead_cows,
                    nucleus_dead_bulls,
                    generation,
                    gens,
                    nucleus_filetag,
                    nucleus_recessives,
                    max_matings=nucleus_max_matings,
                    base_herds=nuclues_base_herds,
                    bull_mating_age=bull_mating_age,
                    cow_mating_age=cow_mating_age,
                    debug=debug,
                    penalty=True,
                    service_bulls=nucleus_service_bulls,
                    edit_prop=edit_prop,
                    edit_type=edit_type,
                    edit_trials=edit_trials,
                    embryo_trials=embryo_trials,
                    embryo_inbreeding=embryo_inbreeding,
                    edit_sex='M',
                    flambda=flambda,
                    bull_criterion=bull_criterion,
                    bull_deficit=bull_deficit,
                    carrier_penalty=carrier_penalty,
                    bull_copies=bull_copies,
                    bull_unique=bull_unique,
                    calf_loss=calf_loss,
                    dehorning_loss=dehorning_loss,
                    harvest_beef=harvest_beef,
                    extras=(cows, bulls, dead_cows, dead_bulls),
                )

                if debug:
                    print 'Next available animal ID after nucleus Pryce + R mating in gen %s:\t\t%s' % \
                          (generation, get_next_id(cows, bulls, dead_cows, dead_bulls, nucleus_cows, nucleus_bulls,
                                                   nucleus_dead_cows, nucleus_dead_bulls))

        # Mate cows to polled bulls whenever they're available.
        elif scenario == 'polled':
            print '\n[run_scenario]: Mating cows to polled bulls using Pryce\'s method at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            cows, bulls, dead_cows, dead_bulls = pryce_mating(
                cows,
                bulls,
                dead_cows,
                dead_bulls,
                generation,
                gens,
                filetag,
                recessives,
                max_matings=max_matings,
                base_herds=base_herds,
                bull_mating_age=bull_mating_age,
                cow_mating_age=cow_mating_age,
                debug=debug,
                penalty=False,
                service_bulls=service_bulls,
                edit_prop=edit_prop,
                edit_type=edit_type,
                edit_trials=edit_trials,
                embryo_trials=embryo_trials,
                embryo_inbreeding=embryo_inbreeding,
                edit_sex='M',
                flambda=flambda,
                bull_criterion=bull_criterion,
                bull_deficit=bull_deficit,
                carrier_penalty=carrier_penalty,
                bull_copies=bull_copies,
                bull_unique=bull_unique,
                calf_loss=calf_loss,
                dehorning_loss=dehorning_loss,
                harvest_beef=harvest_beef,
                extras=(nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls),
            )

            if use_nucleus:
                print '\n[run_scenario]: Mating nucleus cows to polled nucleus bulls using Pryce\'s method at %s' % \
                      datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls = pryce_mating(
                    nucleus_cows,
                    nucleus_bulls,
                    nucleus_dead_cows,
                    nucleus_dead_bulls,
                    generation,
                    gens,
                    nucleus_filetag,
                    nucleus_recessives,
                    max_matings=nucleus_max_matings,
                    base_herds=nucleus_base_herds,
                    bull_mating_age=bull_mating_age,
                    cow_mating_age=cow_mating_age,
                    debug=debug,
                    penalty=False,
                    service_bulls=nucleus_service_bulls,
                    edit_prop=edit_prop,
                    edit_type=edit_type,
                    edit_trials=edit_trials,
                    embryo_trials=embryo_trials,
                    embryo_inbreeding=embryo_inbreeding,
                    edit_sex='M',
                    flambda=flambda,
                    bull_criterion=bull_criterion,
                    bull_deficit=bull_deficit,
                    carrier_penalty=carrier_penalty,
                    bull_copies=bull_copies,
                    bull_unique=bull_unique,
                    calf_loss=nucleus_calf_loss,
                    dehorning_loss=nucleus_dehorning_loss,
                    harvest_beef=nucleus_harvest_beef,
                    extras=(cows, bulls, dead_cows, dead_bulls),
                )

        # Mate cows to polled bulls whenever they're available.
        elif scenario == 'polled_r':
            print '\n[run_scenario]: Mating cows to polled bulls using Pryce\'s method and accounting for recessives at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            cows, bulls, dead_cows, dead_bulls = pryce_mating(
                cows,
                bulls,
                dead_cows,
                dead_bulls,
                generation,
                gens,
                filetag,
                recessives,
                max_matings=max_matings,
                base_herds=base_herds,
                bull_mating_age=bull_mating_age,
                cow_mating_age=cow_mating_age,
                debug=debug,
                penalty=True,
                service_bulls=service_bulls,
                edit_prop=edit_prop,
                edit_type=edit_type,
                edit_trials=edit_trials,
                embryo_trials=embryo_trials,
                embryo_inbreeding=embryo_inbreeding,
                edit_sex='M',
                flambda=flambda,
                bull_criterion=bull_criterion,
                bull_deficit=bull_deficit,
                carrier_penalty=carrier_penalty,
                bull_copies=bull_copies,
                bull_unique=bull_unique,
                calf_loss=calf_loss,
                dehorning_loss=dehorning_loss,
                harvest_beef=harvest_beef,
                extras=(nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls),
            )

            if use_nucleus:
                print '\n[run_scenario]: Mating nucleus cows to polled nucleus bulls using Pryce\'s method and accounting for recessives at %s' % \
                      datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls = pryce_mating(
                    nucleus_cows,
                    nucleus_bulls,
                    nucleus_dead_cows,
                    nucleus_dead_bulls,
                    generation,
                    gens,
                    nucleus_filetag,
                    nucleus_recessives,
                    max_matings=nucleus_max_matings,
                    base_herds=nucleus_base_herds,
                    bull_mating_age=bull_mating_age,
                    cow_mating_age=cow_mating_age,
                    debug=debug,
                    penalty=True,
                    service_bulls=nucleus_service_bulls,
                    edit_prop=edit_prop,
                    edit_type=edit_type,
                    edit_trials=edit_trials,
                    embryo_trials=embryo_trials,
                    embryo_inbreeding=embryo_inbreeding,
                    edit_sex='M',
                    flambda=flambda,
                    bull_criterion=bull_criterion,
                    bull_deficit=bull_deficit,
                    carrier_penalty=carrier_penalty,
                    bull_copies=bull_copies,
                    bull_unique=bull_unique,
                    calf_loss=nucleus_calf_loss,
                    dehorning_loss=nucleus_dehorning_loss,
                    harvest_beef=nucleus_harvest_beef,
                    extras=(cows, bulls, dead_cows, dead_bulls),
                )

        # The default scenario is random mating.
        else:
            cows, bulls, dead_cows, dead_bulls = random_mating(
                cows,
                bulls,
                dead_cows,
                dead_bulls,
                generation,
                generations,
                recessives,
                max_matings=max_matings,
                edit_prop=edit_prop,
                edit_type=edit_type,
                edit_trials=1,
                embryo_trials=1,
                edit_sex='M',
                calf_loss=calf_loss,
                dehorning_loss=dehorning_loss,
                harvest_beef=harvest_beef,
                debug=debug,
                extras=(nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls),
            )

            if debug:
                print 'Next available animal ID after default random mating in gen %s:\t\t%s' % \
                      ( generation, get_next_id(cows, bulls, dead_cows, dead_bulls, nucleus_cows, nucleus_bulls,
                                                nucleus_dead_cows, nucleus_dead_bulls) )


            if use_nucleus:
                nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls = random_mating(
                    nucleus_cows,
                    nucleus_bulls,
                    nucleus_dead_cows,
                    nucleus_dead_bulls,
                    generation,
                    generations,
                    nucleus_recessives,
                    max_matings=nucleus_max_matings,
                    edit_prop=edit_prop,
                    edit_type=edit_type,
                    edit_trials=1,
                    embryo_trials=1,
                    edit_sex='M',
                    calf_loss=nucleus_calf_loss,
                    dehorning_loss=nucleus_dehorning_loss,
                    harvest_beef=nucleus_harvest_beef,
                    debug=debug,
                    extras=(cows, bulls, dead_cows, dead_bulls),
                    )

                if debug:
                    print 'Next available animal ID after default nucleus random mating in gen %s:\t\t%s' % \
                          (generation, get_next_id(cows, bulls, dead_cows, dead_bulls, nucleus_cows, nucleus_bulls,
                                                   nucleus_dead_cows, nucleus_dead_bulls))

        # If we're using nucleus herds go ahead and move bulls from the nucleus to the multiplier herds.
        if use_nucleus:
            print '\t[run_scenario]: Moving nucleus bulls to multiplier herds at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            move_nucleus_bulls_to_multiplier(bulls, nucleus_bulls, generation, nucleus_bulls_to_move, bull_criterion, move_rule='equal',
                                             move_age=1, debug=True)

        # Now print some summary statistics.

        print
        print '\t\t             \tLive\tLive \tLive \tDead\tDead \tDead'
        print '\t\t             \tCows\tBulls\tTotal\tCows\tBulls\tTotal'
        print '\t\tAfter mating:\t%s\t%s\t%s\t%s\t%s\t%s' % (len(cows), len(bulls),
                                                             len(cows)+len(bulls), len(dead_cows),
                                                             len(dead_bulls), len(dead_cows)+len(dead_bulls))

        if use_nucleus:
            print
            print '\t\t             \tLive\tLive \tLive \tDead\tDead \tDead'
            print '\t\t             \tNucl\tNucl \tNucl \tNucl\tNucl \tNucl'
            print '\t\t             \tCows\tBulls\tTotal\tCows\tBulls\tTotal'
            print '\t\tAfter mating:\t%s\t%s\t%s\t%s\t%s\t%s' % (len(nucleus_cows), len(nucleus_bulls),
                                                                 len(nucleus_cows) + len(nucleus_bulls),
                                                                 len(nucleus_dead_cows),
                                                                 len(nucleus_dead_bulls),
                                                                 len(nucleus_dead_cows) + len(nucleus_dead_bulls))

        # Cull bulls
        print '\n[run_scenario]: Computing summary statistics for bulls before culling at %s' %\
              datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        bbull_count, bbull_min, bbull_max, bbull_mean, bbull_var, bbull_std = animal_summary(bulls)
        print '\n[run_scenario]: Culling bulls at %s' % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        bulls, dead_bulls = cull_bulls(bulls, dead_bulls, generation, max_bulls, debug=debug)
        print '\n[run_scenario]: Computing summary statistics for bulls after culling at %s' %\
              datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        abull_count, abull_min, abull_max, abull_mean, abull_var, abull_std = animal_summary(bulls)

        if use_nucleus:
            print '\n[run_scenario]: Computing summary statistics for nucleus bulls before culling at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            n_bbull_count, n_bbull_min, n_bbull_max, n_bbull_mean, n_bbull_var, n_bbull_std = animal_summary(nucleus_bulls)
            print '\n[run_scenario]: Culling nucleus bulls at %s' % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            nucleus_bulls, nucleus_dead_bulls = cull_bulls(nucleus_bulls, nucleus_dead_bulls, generation,
                                                           nucleus_max_bulls, debug=debug)
            print '\n[run_scenario]: Computing summary statistics for nucleus bulls after culling at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            n_abull_count, n_abull_min, n_abull_max, n_abull_mean, n_abull_var, n_abull_std = animal_summary(nucleus_bulls)

        # Count bred and open multiplier cows
        bred_cows = 0
        open_cows = 0
        replacements = 0
        for c in cows:
            age = generation - c['born']
            if age < cow_mating_age:
                # 3 = too young for breeding this generation
                c['bred'] = 3
                replacements += 1
            # only count cows that are of mating age (beef = at least 3 yrs old)
            elif age >= cow_mating_age:
                if c['bred'] == 1:
                    bred_cows += 1
                else:
                    open_cows += 1
            else:
                pass
        print '\n[run_scenario]: %s (multiplier) bred cows in generation %s' % ( bred_cows, generation )
        print '\n[run_scenario]: %s (multiplier) open cows in generation %s' % ( open_cows, generation )
        print '\n[run_scenario]: %s (multiplier) replacements in generation %s' % ( replacements, generation )


        # Count bred and open nucleus cows
        if use_nucleus:
            nucleus_bred_cows = 0
            nucleus_open_cows = 0
            nucleus_replacements = 0
            for c in nucleus_cows:
                age = generation - c['born']
                if age < cow_mating_age:
                    # 3 = too young for breeding this generation
                    c['bred'] = 3
                    nucleus_replacements += 1
                # only count cows that are of mating age (beef = at least 3 yrs old)
                elif age >= cow_mating_age:
                    if c['bred'] == 1:
                        nucleus_bred_cows += 1
                    else:
                        nucleus_open_cows += 1
                else:
                    pass
            print '\n[run_scenario]: %s (nucleus) bred cows in generation %s' % \
            ( nucleus_bred_cows, generation )
            print '\n[run_scenario]: %s (nucleus) open cows in generation %s' % \
            ( nucleus_open_cows, generation )
            print '\n[run_scenario]: %s (multiplier) nucleus_replacements in generation %s' % \
            ( nucleus_replacements, generation )

        # Write cow mating history files (before culling otherwise won't be accurate)
        print '\n[run_scenario]: Writing cow breeding history files before culling cows'
        if use_nucleus:
            write_repro_history_files(nucleus_cows, generation, filetag='_nucleus')
            write_repro_history_files(cows, generation, filetag='_multiplier')
        else:
            write_repro_history_files(cows, generation, filetag='')

        # Cull cows
        print '\n[run_scenario]: Computing summary statistics for cows before culling at %s' % \
              datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        bcow_count, bcow_min, bcow_max, bcow_mean, bcow_var, bcow_std = animal_summary(cows)
        print '\n[run_scenario]: Culling cows at %s' % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        cows, dead_cows = cull_cows(cows, dead_cows, generation, recessives, base_herds, max_cows,
                                    max_cow_age, culling_rate, debug)
        print '\n[run_scenario]: Computing summary statistics for cows after culling at %s' % \
              datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        acow_count, acow_min, acow_max, acow_mean, acow_var, acow_std = animal_summary(cows)

        if use_nucleus:
            print '\n[run_scenario]: Computing summary statistics for nucleus cows before culling at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            n_bcow_count, n_bcow_min, n_bcow_max, n_bcow_mean, n_bcow_var, n_bcow_std = animal_summary(nucleus_cows)
            print '\n[run_scenario]: Culling nucleus cows at %s' % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            nucleus_cows, nucleus_dead_cows = cull_cows(nucleus_cows, nucleus_dead_cows, generation, nucleus_recessives, nucleus_base_herds,
                                                        nucleus_max_cows, nucleus_max_cow_age, culling_rate,
                                                        debug)
            print '\n[run_scenario]: Computing summary statistics for nucleus cows after culling at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            n_acow_count, n_acow_min, n_acow_max, n_acow_mean, n_acow_var, n_acow_std = animal_summary(nucleus_cows)

        print
        print '\t\t              \tLive\tLive \tLive \tDead\tDead \tDead'
        print '\t\t              \tCows\tBulls\tTotal\tCows\tBulls\tTotal'
        print '\t\tAfter culling:\t%s\t%s\t%s\t%s\t%s\t%s' % (len(cows), len(bulls), len(cows)+len(bulls),
                                                              len(dead_cows), len(dead_bulls),
                                                              len(dead_cows)+len(dead_bulls))

        if use_nucleus:
            print
            print '\t\t              \tLive\tLive \tLive \tDead\tDead \tDead'
            print '\t\t              \tNucl\tNucl \tNucl \tNucl\tNucl \tNucl'
            print '\t\t              \tCows\tBulls\tTotal\tCows\tBulls\tTotal'
            print '\t\tAfter culling:\t%s\t%s\t%s\t%s\t%s\t%s' % (len(nucleus_cows), len(nucleus_bulls),
                                                                  len(nucleus_cows) + len(nucleus_bulls),
                                                                  len(nucleus_dead_cows), len(nucleus_dead_bulls),
                                                                  len(nucleus_dead_cows) + len(nucleus_dead_bulls))

        print
        print '\t\tSummary statistics for TBV'
        print '\t\t--------------------------'
        print '\t\t    \t    \tN\tMin\t\tMax\t\tMean\t\tStd'
        print '\t\tBull\tpre \t%s\t%s\t%s\t%s\t%s' % (int(bbull_count), bbull_min, bbull_max, bbull_mean, bbull_std)
        print '\t\tBull\tpost\t%s\t%s\t%s\t%s\t%s' % (int(abull_count), abull_min, abull_max, abull_mean, abull_std)
        print '\t\tCow \tpre \t%s\t%s\t%s\t%s\t%s' % (int(bcow_count), bcow_min, bcow_max, bcow_mean, bcow_std)
        print '\t\tCow \tpost\t%s\t%s\t%s\t%s\t%s' % (int(acow_count), acow_min, acow_max, acow_mean, acow_std)

        if use_nucleus:
            print
            print '\t\tSummary statistics for nucleus herd TBV'
            print '\t\t---------------------------------------'
            print '\t\t    \t    \tN\tMin\t\tMax\t\tMean\t\tStd'
            print '\t\tNucl bull\tpre \t%s\t%s\t%s\t%s\t%s' % (int(n_bbull_count), n_bbull_min, n_bbull_max,
                                                               n_bbull_mean, n_bbull_std)
            print '\t\tNucl bull\tpost\t%s\t%s\t%s\t%s\t%s' % (int(n_abull_count), n_abull_min, n_abull_max,
                                                               n_abull_mean, abull_std)
            print '\t\tNucl cow \tpre \t%s\t%s\t%s\t%s\t%s' % (int(n_bcow_count), n_bcow_min, n_bcow_max, n_bcow_mean,
                                                               bcow_std)
            print '\t\tNucl cow \tpost\t%s\t%s\t%s\t%s\t%s' % (int(n_acow_count), n_acow_min, n_acow_max, n_acow_mean,
                                                               acow_std)

        # Now update the MAF for the recessives in the population
        print '\n[run_scenario]: Updating minor allele frequencies at %s' %\
              datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        recessives, freq_hist = update_maf(cows, bulls, generation, recessives, freq_hist,
                                           show_recessives)
        if use_nucleus:
            print '\n[run_scenario]: Updating nucleus herd minor allele frequencies at %s' % \
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            nucleus_recessives, nucleus_freq_hist = update_maf(nucleus_cows, nucleus_bulls, generation,
                                                               nucleus_recessives, nucleus_freq_hist,
                                                               show_recessives)

        # Write history files.
        print '\n[run_scenario]: Writing history files at %s' % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        if history_freq == 'end' and generation == gens:
            write_history_files(cows, bulls, dead_cows, dead_bulls, generation, filetag)
            if use_nucleus:
                write_history_files(nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls,
                                    generation, nucleus_filetag)
            if show_disposals:
                print_death_codes()
                print '\n[run_scenario]: Disposal reasons for multiplier herd animals.'
                disposal_reasons(dead_bulls, dead_cows)

                print '\n[run_scenario]: Count of open-0, bred-1 and replacement-3 multiplier cows.'
                bred_count(cows)

                if use_nucleus:
                    print '\n[run_scenario]: Disposal reasons for nucleus herd animals.'
                    disposal_reasons(nucleus_dead_bulls, nucleus_dead_cows)

                    print '\n[run_scenario]: Count of open-0, bred-1 and replacement-3 nucleus cows.'
                    bred_count(nucleus_cows)

        elif history_freq == 'end' and generation != gens:
            pass

        else:
            write_history_files(cows, bulls, dead_cows, dead_bulls, generation, filetag)
            if use_nucleus:
                write_history_files(nucleus_cows, nucleus_bulls, nucleus_dead_cows, nucleus_dead_bulls,
                                    generation, nucleus_filetag)
            if show_disposals:
                print_death_codes()
                print '\n[run_scenario]: Disposal reasons for multiplier herd animals.'
                disposal_reasons(dead_bulls, dead_cows)

                print '\n[run_scenario]: Count of open-0, bred-1 and replacement-3 multiplier cows.'
                bred_count(cows)

                if use_nucleus:
                    print '\n[run_scenario]: Disposal reasons for nucleus herd animals.'
                    disposal_reasons(nucleus_dead_bulls, nucleus_dead_cows)
                    print '\n[run_scenario]: Count of open-0, bred-1 and replacement-3 nucleus cows.'
                    bred_count(nucleus_cows)


    # Save the simulation parameters so that we know what we did.
    write_parameter_file(scenario, cow_mean, genetic_sd, bull_diff, polled_diff, gens, percent, base_bulls,
                         base_max_bull_age, base_cows, base_max_cow_age, service_bulls, base_herds, bull_mating_age,
                         cow_mating_age, max_bulls, max_bull_age, max_cows, max_cow_age, debug, filetag, recessives,
                         max_matings, rng_seed, show_recessives, history_freq, edit_prop, edit_type, edit_trials,
                         embryo_trials, embryo_inbreeding, flambda, bull_criterion, bull_deficit, base_polled,
                         carrier_penalty, bull_copies, polled_parms, bull_unique, calf_loss, nucleus_calf_loss,
                         dehorning_loss, nucleus_dehorning_loss, harvest_beef, nucleus_harvest_beef, culling_rate,
                         check_all_parms, show_disposals, use_nucleus, nucleus_cow_mean, nucleus_genetic_sd,
                         nucleus_bull_diff, nucleus_base_bulls, nucleus_base_max_bull_age, nucleus_base_cows,
                         nucleus_base_max_cow_age, nucleus_base_herds, nucleus_service_bulls, nucleus_max_bulls,
                         nucleus_max_bull_age, nucleus_max_cows, nucleus_max_cow_age, nucleus_max_matings,
                         nucleus_bulls_to_move, nucleus_filetag, nucleus_recessives)

    # Save the minor allele frequencies to a file.
    if use_nucleus:
        write_allele_freqs(freq_hist, nucleus_freq_hist, filetag, nucleus_filetag)
    else:
        write_allele_freqs(freq_hist, {}, filetag, '')

    # Save death reasons history to file
    write_death_reasons_history_file(dead_cows, dead_bulls, filetag)
    if use_nucleus:
        write_death_reasons_history_file(nucleus_dead_cows, nucleus_dead_bulls, nucleus_filetag)

    # Now that we're done with the simulation let's go ahead and visualize the change in minor allele frequency
    fig = plt.figure()
    ax = fig.add_subplot(1, 1, 1)
    ax.set_ylim(0.0, 1.0)
    ax.set_title("Allele frequency change over time (%s)" % scenario)
    ax.set_xlabel("Generation")
    ax.set_ylabel("Allele frequency")
    x = freq_hist.keys()
    colors = itertools.cycle(['r', 'g', 'b', 'c', 'y', 'm', 'k'])
    markers = itertools.cycle(['o', 's', 'v'])
    for rk, rv in recessives.iteritems():
        r = recessives.keys().index(rk)
        y = []
        for v in freq_hist.values():
            y.append(v[r])
        ax.plot(x, y, color=colors.next(), marker=markers.next(), label=rk)
    ax.legend(loc='best')
    filename = "allele_frequency_plot%s.png" % filetag
    plt.savefig(filename, bbox_inches="tight")
    plt.clf()


# Print death codes in case you can't remember them all I know I can't)
#
# 02/26/2019    JBC    Added MM's "harvested for beef" code.

def print_death_codes():
    print """Codes used to indicate reasons for animal death:
    \tA = Animal culled for age
    \tB = Animal was harvested for beef
    \tC = Animal died as a calf
    \tH = Animal died due to complications from dehorning
    \tI = Animal involuntarily culled
    \tN = Animal culled to maintain population size
    \tR = Animal killed by recessive genetic condition
    """


# Run checks on the parameters to make sure that they've been given plausible values.
#
# 02/26/2019    JBC    Added 'base_max_bull_age', 'max_bull_age', 'base_max_cow_age',
#                      'max_cow_age', fixed 'nucleus_recessives' checking.


def check_parameters(scenario, cow_mean, genetic_sd, bull_diff, polled_diff, gens, percent, base_bulls,
                     base_max_bull_age, max_bull_age, base_cows, base_max_cow_age, max_cow_age,
                     service_bulls, base_herds, bull_mating_age, cow_mating_age, max_bulls, max_cows, debug, filetag,
                     recessives, max_matings, rng_seed, show_recessives, history_freq, edit_prop,
                     edit_type, edit_trials, embryo_trials, embryo_inbreeding, flambda, bull_criterion,
                     bull_deficit, base_polled, carrier_penalty, bull_copies, polled_parms, bull_unique,
                     calf_loss, nucleus_calf_loss, dehorning_loss, nucleus_dehorning_loss, harvest_beef, nucleus_harvest_beef,
                     culling_rate, show_disposals, use_nucleus,
                     nucleus_cow_mean, nucleus_genetic_sd, nucleus_bull_diff, nucleus_base_bulls, nucleus_base_max_bull_age,
                     nucleus_base_cows, nucleus_base_max_cow_age, nucleus_base_herds, nucleus_service_bulls, nucleus_max_bulls,
                     nucleus_max_bull_age, nucleus_max_cows, nucleus_max_cow_age, nucleus_max_matings, nucleus_bulls_to_move, nucleus_filetag,
                     nucleus_recessives):

    """Check simulation parameters to ensure they contain permissible values.

    :param scenario: The mating strategy to use in the current scenario ('random'|'trunc'|'pryce').
    :type scenario: string
    :param cow_mean: Average base population cow TBV.
    :type cow_mean: float
    :param genetic_sd: Additive genetic SD of the simulated trait.
    :type genetic_sd: float
    :param bull_diff: Differential between base cows and bulls, in genetic SD.
    :type bull_diff: float
    :parm polled_diff: Difference between Pp and pp bulls, and PP and pp bulls, in genetic SD.
    :type polled_diff: List of floats
    :param gens: Total number of generations to run the simulation.
    :type gens: int
    :param percent: Percent of bulls to use as sires in the truncation mating scenario.
    :type percent: float
    :param base_bulls: The number of bulls in the base population.
    :type base_bulls: int
    :base_max_bull_age: How long base population bulls live.
    :type base_max_bull_age: int
    :param max_bull_age: How long bulls live.
    :type max_bull_age: int
    :param base_cows: The number of cows in the base population.
    :type base_cows: int
    :base_max_cow_age: How long base population cows live.
    :type base_max_cow_age: int
    :param max_cow_age: How long cows live.
    :type max_cow_age: int
    :param service_bulls: The number of herd bulls to use in each herd each generation.
    :type service_bulls: int
    :param base_herds: The number of herds in the population.
    :type base_herds: int
    :param bull_mating_age: The minimum age for bulls to breed cows.
    :type generation: int
    :param cow_mating_age: The minimum age for cows to be bred and have a calf in the same generation.
    :type generation: int
    :param max_bulls: The maximum number of bulls that can be alive at one time.
    :type max_bulls: int
    :param max_cows: The maximum number of cows that can be alive at one time.
    :type max_cows: int
    :param debug: Boolean. Activate/deactivate debugging messages.
    :type debug: bool
    :param filetag: Added to file names to describe the analysis a file is associated with.
    :type filetag: string
    :param recessives: Dictionary of recessive alleles in the population.
    :type recessives: dictionary
    :param max_matings: The maximum number of matings permitted for each bull.
    :type max_matings: int
    :param show_recessives: Boolean. Print summary information for each recessive.
    :type show_recessives: bool
    :param history_freq: When 'end', save only files from final generation, else save every generation.
    :type history_freq: string
    :param edit_prop: The proportion of animals to edit based on TBV (e.g., 0.01 = 1 %).
    :type edit_prop: list
    :param edit_type: Tool used to edit genes: 'Z' = ZFN, 'T' = TALEN, 'C' = CRISPR, 'P' = no errors.
    :type edit_type: char
    :param edit_trials: The number of attempts to edit an embryo successfully (-1 = repeat until success).
    :type edit_trials: int
    :param embryo_trials: The number of attempts to transfer an edited embryo successfully (-1 = repeat until success).
    :type embryo_trials: int
    :param embryo_inbreeding: Write a file of coefficients of inbreeding for all possible bull-by-cow matings.
    :type embryo_inbreeding: boolean
    :param flambda: Decrease in economic merit (in US dollars) per 1% increase in inbreeding.
    :type flambda: float
    :param bull_criterion: Criterion used to select the group of bulls for mating.
    :type bull_criterion: string
    :param bull_deficit: Manner of handling too few bulls for matings: 'use_horned' or 'no_limit'.
    :type bull_deficit: string
    :param base_polled: Genotype of polled animals in the base population ('homo'|'het'|'both')
    :type base_polled: string
    :return: Nothing is returned from this function.
    :param carrier_penalty: Penalize carriers for carrying a copy of an undesirable allele (True), or not (False)
    :rtype carrier_penalty: bool
    :param bull_copies: Genotype of polled bulls selected for mating (0|1|2|4|5|6)
    :type bull_copies: integer
    :param polled_parms: Proportion of polled bulls, proportion of PP, and proportion of Pp bulls.
    :type polled_parms: list of floats
    :param calf_loss: Proportion of calves that die before they reach 1 year of age.
    :type calf_loss: float
    :param dehorning_loss: The proportion of cows that die during dehorning.
    :type dehorning_loss: float
    :param culling_rate: The proportion of cows culled involuntarily each generation.
    :type culling_rate: float
    :param show_disposals: Print a summary of disposal reasons following the history_freq flag.
    :type show_disposals: bool
    :param use_nucleus: Create and use nucleus herds to propagate elite genetics
    :type use_nucleus: bool
    :param nucleus_cow_mean: Average nucleus cow TBV.
    :type nucleus_cow_mean: float
    :param nucleus_genetic_sd: Additive genetic SD of the simulated trait.
    :type nucleus_genetic_sd: float
    :param nucleus_bull_diff: Differential between nucleus cows and bulls, in genetic SD
    :type nucleus_bull_diff: float
    :param nucleus_base_bulls: Initial number of bulls in nucleus herds
    :type nucleus_base_bulls: int
    :param nucleus_base_cows: Initial number of cows in nucleus herds
    :type nucleus_base_cows: int
    :param nucleus_base_herds: Number of nucleus herds in the population
    :type nucleus_base_herds: int
    :param nucleus_service_bulls: Number of bulls to use in each nucleus herd each generation.
    :type nucleus_service_bulls: int
    :param nucleus_max_bulls: Maximum number of live bulls to keep each generation in nucleus herds
    :type nucleus_max_bulls: int
    :param nucleus_max_cows: Maximum number of live cows to keep each generation in nucleus herds
    :type nucleus_max_cows: int
    :param nucleus_max_matings: The maximum number of matings permitted for each nucleus herd bull
    :type nucleus_max_matings: int
    :param nucleus_bulls_to_move: The number of bulls to move from nucleus herds to the multiplier herds each year
    :type nucleus_bulls_to_move: int
    :param nucleus_filetag: Added to file names to describe the analysis a nucleus herd file is associated with.
    :type nucleus_filetag: string
    :param nucleus_recessives: Dictionary of recessive alleles in the nucleus population.
    :type nucleus_recessives: dictionary
    :rtype: None
    """

    # Setup Cerberus rules for validating parameters
    ge_schema = {
        'scenario': {'type': 'string', 'allowed': ['random', 'truncation', 'pryce', 'pryce_r',
                                                   'polled', 'polled_r']},
        'cow_mean': {'type': 'number'},
        'genetic_sd': {'type': 'number'},
        'bull_diff': {'type': 'number'},
        'polled_diff': {'type': 'list', 'minlength': 2, 'maxlength': 2, 'schema': {'type': 'number'}},
        'gens': {'type': 'integer', 'min': 0},
        'percent': {'type': 'float', 'min': 0.0, 'max': 1.0},
        'base_bulls': {'type': 'integer', 'min': 0},
        'base_max_bull_age': {'type': 'integer', 'min': 1},
        'max_bull_age': {'type': 'integer', 'min': 1},
        'base_cows': {'type': 'integer', 'min': 0},
        'base_max_cow_age': {'type': 'integer', 'min': 1},
        'max_cow_age': {'type': 'integer', 'min': 1},
        'service_bulls': {'type': 'integer', 'min': 0},
        'base_herds': {'type': 'integer', 'min': 0},
        'bull_mating_age': {'type': 'integer', 'min': 1},
        'cow_mating_age': {'type': 'integer', 'min': 1},
        'max_bulls': {'type': 'integer', 'min': 0},
        'max_cows': {'type': 'integer', 'min': 0},
        'debug': {'type': 'boolean'},
        'filetag': {'type': 'string', 'empty': True},
        'recessives': {'type': 'dict'},
        'max_matings': {'type': 'integer', 'min': 0},
        'rng_seed': {'type': ['string', 'integer']},
        'show_recessives': {'type': 'boolean'},
        'history_freq': {'type': 'string'},
        'edit_prop': {'type': 'list', 'minlength': 2, 'maxlength': 2,
                      'schema': {'type': 'float', 'min': 0.0, 'max': 1.0}},
        'edit_type': {'type': 'string', 'allowed': ['Z', 'T', 'C', 'P']},
        'edit_trials': {'type': 'integer', 'min': -1},
        'embryo_trials': {'type': 'integer', 'min': -1},
        'embryo_inbreeding': {'type': 'boolean'},
        'flambda': {'type': 'number'},
        'bull_criterion': {'type': 'string', 'allowed': ['polled', 'random']},
        'bull_deficit': {'type': 'string', 'allowed': ['use_horned', 'no_limit', 'open']},
        'base_polled': {'type': 'string', 'allowed': ['homo', 'het', 'both']},
        'carrier_penalty': {'type': 'boolean'},
        'bull_copies': {'type': 'integer'},
        'polled_parms': {'type': 'list', 'minlength': 3, 'maxlength': 3,
                      'schema': {'type': 'float', 'min': 0.0, 'max': 1.0}},
        'bull_unique': {'type': 'boolean'},
        'calf_loss': {'type': 'float', 'min': 0.0, 'max': 1.0},
        'nucleus_calf_loss': {'type': 'float', 'min': 0.0, 'max': 1.0},
        'dehorning_loss': {'type': 'float', 'min': 0.0, 'max': 1.0},
        'nucleus_dehorning_loss': {'type': 'float', 'min': 0.0, 'max': 1.0},
        'harvest_beef': {'type': 'float', 'min': 0.0, 'max': 1.0},
        'nucleus_harvest_beef': {'type': 'float', 'min': 0.0, 'max': 1.0},
        'culling_rate': {'type': 'float', 'min': 0.0, 'max': 1.0},
        'show_disposals': {'type': 'boolean'},
        'use_nucleus': {'type': 'boolean'},
        'nucleus_cow_mean': {'type': 'number'},
        'nucleus_genetic_sd': {'type': 'number'},
        'nucleus_bull_diff': {'type': 'number'},
        'nucleus_base_bulls': {'type': 'integer', 'min': 0},
        'nucleus_base_max_bull_age': {'type': 'integer', 'min': 0},
        'nucleus_base_cows': {'type': 'integer', 'min': 0},
        'nucleus_base_max_cow_age': {'type': 'integer', 'min': 0},
        'nucleus_base_herds': {'type': 'integer', 'min': 0},
        'nucleus_service_bulls': {'type': 'integer', 'min': 0},
        'nucleus_max_bulls': {'type': 'integer', 'min': 0},
        'nucleus_max_bull_age': {'type': 'integer', 'min': 0},
        'nucleus_max_cows': {'type': 'integer', 'min': 0},
        'nucleus_max_cow_age': {'type': 'integer', 'min': 0},
        'nucleus_max_matings': {'type': 'integer', 'min': 0},
        'nucleus_bulls_to_move': {'type': 'integer', 'min': 0},
        'nucleus_filetag': {'type': 'string', 'empty': True},
        'nucleus_recessives': {'type': 'dict'},
    }

    ge_document = { 'scenario': scenario, 'cow_mean': cow_mean, 'genetic_sd': genetic_sd,
                    'bull_diff': bull_diff, 'polled_diff': polled_diff, 'gens': gens,
                    'percent': percent, 'base_bulls': base_bulls, 'base_max_bull_age': base_max_bull_age,
                    'max_bull_age': max_bull_age, 'base_cows': base_cows, 'base_max_cow_age': base_max_cow_age,
                    'max_cow_age': max_cow_age, 'service_bulls': service_bulls, 'base_herds': base_herds,
                    'max_bulls': max_bulls, 'max_cows': max_cows, 'debug': debug, 'filetag': filetag,
                    'recessives': recessives, 'max_matings': max_matings, 'rng_seed': rng_seed,
                    'show_recessives': show_recessives, 'history_freq': history_freq, 'edit_prop': edit_prop,
                    'edit_type': edit_type, 'edit_trials': edit_trials, 'embryo_trials': embryo_trials,
                    'embryo_inbreeding': embryo_inbreeding, 'flambda': flambda, 'bull_criterion': bull_criterion,
                    'bull_deficit': bull_deficit, 'base_polled': base_polled, 'carrier_penalty': carrier_penalty,
                    'bull_copies': bull_copies, 'polled_parms': polled_parms, 'bull_unique': bull_unique,
                    'calf_loss': calf_loss, 'nucleus_calf_loss': nucleus_calf_loss, 'dehorning_loss': dehorning_loss,
                    'nucleus_dehorning_loss': nucleus_dehorning_loss, 'harvest_beef': harvest_beef,
                    'nucleus_harvest_beef': nucleus_harvest_beef, 'culling_rate': culling_rate,
                    'show_disposals': show_disposals, 'use_nucleus': use_nucleus, 'nucleus_cow_mean': nucleus_cow_mean,
                    'nucleus_genetic_sd': nucleus_genetic_sd, 'nucleus_bull_diff': nucleus_bull_diff,
                    'nucleus_base_bulls': nucleus_base_bulls, 'nucleus_base_max_bull_age': nucleus_base_max_bull_age,
                    'nucleus_base_cows': nucleus_base_cows, 'nucleus_base_max_cow_age': nucleus_base_max_cow_age,
                    'nucleus_base_herds': nucleus_base_herds, 'nucleus_service_bulls': nucleus_service_bulls,
                    'nucleus_max_bulls': nucleus_max_bulls, 'nucleus_max_bull_age': nucleus_max_bull_age,
                    'nucleus_max_cows': nucleus_max_cows, 'nucleus_max_cow_age': nucleus_max_cow_age,
                    'nucleus_max_matings': nucleus_max_matings, 'nucleus_bulls_to_move': nucleus_bulls_to_move,
                    'nucleus_filetag': nucleus_filetag, 'nucleus_recessives': nucleus_recessives
    }

    ge_validator = cerberus.Validator(ge_schema)
    ge_result = ge_validator(ge_document)
    if not ge_result:
        print '[check_parameters]: Validation failed, errors follow.'
        print '\n\tParameter\tError'
        print '\t' + '-'*60
        for ge_k, ge_v in ge_validator.errors.iteritems():
            print '\t%s\t%s' % ( ge_k, ge_v )
        print ''

    return ge_result

def jump():
    pass

if __name__ == '__main__':

    # Simulation parameters

    # -- Program Control Parameters
    debug =         True        # Activate (True) or deactivate (False) debugging messages
    rng_seed =      long(time.time()) + os.getpid() 	# Use the current time to generate the RNG seed so that we can recreate the
                                                        # simulation if we need/want to.
    #rng_seed = 419
    embryo_inbreeding = False          # Save file with embryo inbreeding (makes large files!!!)
    history_freq =      'end'          # Only write history files at the end of the simulation, not every generation.
    show_recessives =   False          # Show recessive frequencies after each round.
    check_all_parms =   True           # Perform a formal check on all parameters.
    show_disposals =    True           # Print the frequency of disposals by reason.
    filetag =           '_test32'      # Label with which to append filenames for tracking different scenarios.
    nucleus_filetag =   '_nuc_test32'  # Label with which to append filenames for tracking different scenarios
    generations =       5              # How long to run the simulation

    # -- Base Population (Multiplier) Parameters
    cow_mean =      0.       # Average base population cow TBV.
    genetic_sd =    200.     # Additive genetic SD of the simulated trait.
    bull_diff =     1.5      # Differential between base cows and bulls, in genetic SD
    base_bulls =    1200     # Initial number of founder bulls in the population
    base_max_bull_age = 10   # Maximum age of base population bulls
    base_cows =     35000    # Initial number of founder cows in the population
    base_max_cow_age = 5     # Maximum age of base population cows
    base_herds =    200      # Number of herds in the population
    base_polled =   'both'   # Genotype of polled animals in the base population ('homo'|'het'|'both')
    polled_parms = [0.33,    # Proportion of polled bulls, proportion of PP, and proportion of Pp bulls.
                    0.08,
                    0.92]
    polled_diff =  [1.0,     # Differential between Pp and pp bulls, in genetic CD
                    1.3]     # Differential between PP and pp bulls, in genetic CD


    # -- Scenario (Multiplier) Parameters
    service_bulls =   15       # Number of herd bulls to use in each herd each generation.
    max_bulls =       3100     # Maximum number of live bulls to keep each generation
    max_bull_age =    10       # Age of bulls beyond which they're culled
    bull_mating_age = 2        # The minimum age for bulls to breed cows (set to 1 for dairy).
    max_cows =        100000   # Maximum number of live cows to keep each generation
    max_cow_age =     5        # Age of cows beyond which they're culled
    cow_mating_age =  3        # The minimum age for cows to be bred and have a calf in the same generation (set to 1 for dairy).
    percent =         0.10     # Proportion of bulls to use in the truncation mating scenario
    max_matings =     35       # The maximum number of matings permitted for each bull (5% of cows)
    bull_criterion =  'random' # How should the bulls be picked?.
    bull_deficit =    'open'   # Manner of handling too few polled bulls for matings: 'use_horned' or 'no_limit' or 'open'
    bull_unique =     True     # Create unique bull portfolios, if possible, for each herd.
    bull_copies =     4        # Genotype of polled bulls selected for mating ('homo'|'het'|'both') # 4
    flambda =         25.      # Decrease in economic merit (in US dollars) per 1% increase in inbreeding.
    carrier_penalty = True     # Penalize carriers for carrying a copy of an undesirable allele (True), or not (False)
    calf_loss =       0.13     # Rate of calfhood mortality.
    dehorning_loss =  0.02     # Rate of mortality during dehorning.
    harvest_beef =    1.0      # Percentage of (multiplier) males that are harvested at ~1 yr of age for Beef

    culling_rate =    0.0      # Involuntary culling rate for cows.


    # -- Nucleus Herd Parameters
    use_nucleus =            True   # Create and use nucleus herds to propagate elite genetics
    nucleus_cow_mean =       200.   # Average nucleus cow TBV.
    nucleus_genetic_sd =     200.   # Additive genetic SD of the simulated trait.
    nucleus_bull_diff =      1.5    # Differential between nucleus cows and bulls, in genetic SD
    nucleus_base_bulls =     60     # Initial number of bulls in nucleus herds
    nucleus_base_max_bull_age = 10  # Age of bulls beyond which they're culled
    nucleus_base_cows =      1500   # Initial number of cows in nucleus herds
    nucleus_base_max_cow_age = 3    # Age of cows beyond which they're culled
    nucleus_base_herds =     10     # Number of nucleus herds in the population
    nucleus_service_bulls =  15     # Number of bulls to use in each nucleus herd each generation.
    nucleus_max_bulls =      100    # Maximum number of live bulls to keep each generation in nucleus herds
    nucleus_max_bull_age =   10     # Age of nucleus bulls beyond which they're culled
    nucleus_max_cows =       3000   # Maximum number of live cows to keep each generation in nucleus herds
    nucleus_max_cow_age =    3      # Age of nucleus cows beyond which they're culled
    nucleus_max_matings =    35     # The maximum number of matings permitted for each nucleus herd bull
    nucleus_bulls_to_move =  1200   # The number of bulls to move from nucleus herds to the multiplier herds each year
    nucleus_calf_loss =      0.08   # Rate of calfhood mortality for nucleus herds.
    nucleus_dehorning_loss = 0.02   # Rate of mortality during dehorning.
    nucleus_harvest_beef =   0.0    # Percentage of (nucleus) males that are harvested at ~1 yr of age for Beef


    # -- Gene Editing Parameters
    edit_prop =     [0.00, 0.00]   # The proportion of animals to edit based on TBV (e.g., 0.01 = 1 %),
                                   # the first value is for males and the second for females.
    edit_type =     'C'            # The type of tool used to edit genes -- 'Z' = ZFN, 'T' = TALEN,
                                   # 'C' = CRISPR, 'P' = perfect (no failures/only successes).
    edit_trials =   -1             # The number of attempts to edit an embryo successfully (-1 = repeat until success).
    embryo_trials = -1             # The number of attempts to transfer an edited embryo successfully(-1 = repeat until success).


    # -- Definition of Recessives in the Population
    # Recessives are stored in a list of lists. The first value in each list is the minor allele frequency in the base
    # population, and the second number is the economic value of the minor allele. If the economic value is $20, that
    # means that the value of an affected homozygote is -$20. The third value in the record indicates if the recessive
    # is lethal (0) or non-lethal (0). The fourth value is a label that is not used for any calculations. The fifth
    # value is  a flag which indicates a gene should be left in its original state (0) or edited (1). The sixth value
    # indicates the type of change ("D" = deactivation/knock-out, "A" = addition/insertion, "O" = use the same values
    # as pre-August 30 versions of the program) needed to edit the allele.
    # recessives = [
    #     [0.0276, 150, 1, 'Brachyspina', 1, 'A'],
    #     [0.0192,  40, 1, 'HH1', 1, 'A'],
    #     [0.0166,  40, 1, 'HH2', 1, 'A'],
    #     [0.0295,  40, 1, 'HH3', 1, 'A'],
    #     [0.0037,  40, 1, 'HH4', 1, 'A'],
    #     [0.0222,  40, 1, 'HH5', 1, 'A'],
    #     [0.0025, 150, 1, 'BLAD', 1, 'A'],
    #     [0.0137,  70, 1, 'CVM', 1, 'A'],
    #     [0.0001,  40, 1, 'DUMPS', 1, 'A'],
    #     [0.0007, 150, 1, 'Mulefoot', 1, 'A'],
    #     [0.9929,  40, 0, 'Horned', 1, 'D'],
    #     [0.0542, -20, 0, 'Red', 0, 'D'],
    # ]

    recessives = {
        'Horned': {'frequency': 0.75, 'value': 50, 'lethal': 0, 'edit': 0, 'edit_mode': 'D'},
    }

    # A separate dictionary is used for recessives in the nucleus herds so that editing can be controlled differently
    # for the nucleus and multiplier herds. In this case, edit is '1' for the nucleus herds and '0' for the
    # multiplier herds, so editing only happens in the nucleus.
    nucleus_recessives = {
        'Horned': {'frequency': 0.75, 'value': 50, 'lethal': 0, 'edit': 1, 'edit_mode': 'D'},
    }

    # Alternatively, you can read the recessive information from a file.
    #with open('../recessives.config','r') as inf:
    #    default_recessives = ast.literal_eval(inf.read())
    # recessives = copy.deepcopy(default_recessives)


    run_scenario(scenario='polled_r',
                 cow_mean=cow_mean,
                 genetic_sd=genetic_sd,
                 bull_diff=bull_diff,
                 polled_diff=polled_diff,
                 gens=generations,
                 percent=percent,
                 base_bulls=base_bulls,
                 base_max_bull_age=base_max_bull_age,
                 base_cows=base_cows,
                 base_max_cow_age=base_max_cow_age,
                 service_bulls=service_bulls,
                 base_herds=base_herds,
                 bull_mating_age=bull_mating_age,
                 cow_mating_age=cow_mating_age,
                 max_bulls=max_bulls,
                 max_cows=max_cows,
                 debug=debug,
                 filetag=filetag,
                 recessives=recessives,
                 max_matings=max_matings,
                 rng_seed=rng_seed,
                 show_recessives=show_recessives,
                 history_freq=history_freq,
                 edit_prop=edit_prop,
                 edit_type=edit_type,
                 edit_trials=edit_trials,
                 embryo_trials=embryo_trials,
                 embryo_inbreeding=embryo_inbreeding,
                 flambda=flambda,
                 bull_criterion=bull_criterion,
                 bull_deficit=bull_deficit,
                 base_polled = base_polled,
                 carrier_penalty=carrier_penalty,
                 bull_copies=bull_copies,
                 polled_parms=polled_parms,
                 bull_unique=bull_unique,
                 calf_loss=calf_loss,
                 nucleus_calf_loss=nucleus_calf_loss,
                 dehorning_loss=dehorning_loss,
                 nucleus_dehorning_loss=nucleus_dehorning_loss,
                 harvest_beef=harvest_beef,
                 nucleus_harvest_beef=nucleus_harvest_beef,
                 culling_rate=culling_rate,
                 check_all_parms=check_all_parms,
                 use_nucleus=use_nucleus,
                 nucleus_cow_mean=nucleus_cow_mean,
                 nucleus_genetic_sd=nucleus_genetic_sd,
                 nucleus_bull_diff=nucleus_bull_diff,
                 nucleus_base_bulls=nucleus_base_bulls,
                 nucleus_base_max_bull_age=nucleus_base_max_bull_age,
                 nucleus_base_cows=nucleus_base_cows,
                 nucleus_base_max_cow_age=nucleus_base_max_cow_age,
                 nucleus_base_herds=nucleus_base_herds,
                 nucleus_service_bulls=nucleus_service_bulls,
                 nucleus_max_bulls=nucleus_max_bulls,
                 nucleus_max_bull_age=nucleus_max_bull_age,
                 nucleus_max_cows=nucleus_max_cows,
                 nucleus_max_cow_age=nucleus_max_cow_age,
                 nucleus_max_matings=nucleus_max_matings,
                 nucleus_bulls_to_move=nucleus_bulls_to_move,
                 nucleus_recessives=nucleus_recessives,
                 nucleus_filetag=nucleus_filetag,
                 )
