from code_scripts.TreeExtraction_class import TreeExtraction
from sklearn.model_selection import ParameterGrid
from code_scripts.utilities import plot_preselected_trees, rule_print_inline
from code_scripts.utilities import rule_to_file, frmt_preds_to_print
from joblib import Parallel, delayed
import os
os.environ["OMP_NUM_THREADS"] = '1' # avoids memory leak caused by K-Means
import warnings
import numpy as np
import sklearn
from sklearn.utils.validation import check_is_fitted
import sksurv
from code_scripts.wrapper_class import EnsembleWrapper
from code_scripts.utilities import predict_helper
import matplotlib.pyplot as plt
from sksurv.ensemble import RandomSurvivalForest

class Bellatrex:
    
    FONT_SIZE = 14
    MAX_FEATURE_PRINT = 10
    
    def __init__(self, clf, set_up="auto", 
                 force_refit=False,
                 verbose=0,
                 proj_method="PCA",
                 dissim_method="rules",
                 feature_represent="weighted",
                 p_grid = {"n_trees": [0.2, 0.5, 0.8],
                           "n_dims": [2, 5, None],
                           "n_clusters": [1,2,3],
                             },
                 
                 pre_select_trees="L2",
                 fidelity_measure="L2",
                 n_jobs=1,
                 plot_GUI=False,
                 plot_max_depth=None,
                 dpi_figure=90,
                 show=True,
                 colormap=None,
                 ys_oracle = None):
        
        self.clf = clf #(un)fitted instance of R(S)F
        self.set_up = set_up 
        self.force_refit = force_refit
        self.proj_method = proj_method
        self.dissim_method = dissim_method
        self.feature_represent = feature_represent
        self.p_grid = p_grid
        self.pre_select_trees = pre_select_trees
        self.fidelity_measure = fidelity_measure
        self.n_jobs = n_jobs
        self.verbose = verbose
        self.plot_GUI = plot_GUI
        self.plot_max_depth=plot_max_depth
        self.dpi_figure=dpi_figure
        self.colormap=colormap
        self.show = show
        self.ys_oracle = None
        
    def _validate_p_grid(self):
        
        """
        This method validates and sets the parameters for the hyperparameter grid.
        It checks if the provided keys in the p_grid dictionary are valid,
        and sets default values if any of them are missing. 
        It also checks if the values provided for n_trees are valid and raises 
        errors if necessary. Finally, it converts n_trees to the number 
        of trees used by the underlying ensemble model in case the values
        for n_trees are given as proportions.
        
        Raises:
            ValueError: If n_trees is less than or equal to 0, or if the list of n_trees contains both
                        proportions and integers, or if any n_trees value is greater than n_estimators.
            Warning: If the hyperparameter list contains unexpected keys other
            than the default set, this function reverts to using default values.
        """
        
        default_set_keys = set(["n_trees", "n_dims", "n_clusters"])

        if set(self.p_grid.keys()) != set(["n_trees", "n_dims", "n_clusters"]):
            warnings.warn("The hyperparameter list contains unexpected keys,"
                          "other from {}. Ignoring extra parameters".format(default_set_keys))
    
        if "n_trees" not in self.p_grid.keys():
            self.n_trees = [0.2, 0.5, 0.8] # set to default if not existing
        else:
            self.n_trees = self.p_grid["n_trees"]           #CAN BE A LIST

        if "n_dims" not in self.p_grid.keys():
            self.n_dims = [None] # set to default if not existing
        else:
            self.n_dims = self.p_grid["n_dims"]             #CAN BE A LIST
    
        if "n_clusters" not in self.p_grid.keys():
            self.n_clusters = [1, 2, 3] # set to default if not existing
        else:
            self.n_clusters = self.p_grid["n_clusters"]     # CAN BE A LIST
        
        if min(self.n_trees) <= 0:
            raise ValueError("n_trees must be all > 0")
        
        if min(self.n_trees) < 1.0 and max(self.n_trees) > 1.0:
            raise ValueError('The list of n_trees must either indicate a proportion'
                             ' of trees in the (0,1] interval, or indicate the number'
                             ' of tree learners.')
            
        # Check that the n_trees provided by the user does not exceed the number of total trees in the R(S)F
        # this works for both a fitted sklearn model and a dictionary    
        
        # tot_estimators = self.clf['n_estimators'] if isinstance(self.clf, dict) else self.clf.n_estimators
        tot_estimators = self.clf.n_estimators
        
        if max(self.n_trees) > tot_estimators: 
            raise ValueError("n_trees cannot be greater than n_estimators")        
        
        #if proportions fo trees are given correctly, as expected
        # transform them to integer values for later steps
        if np.array([isinstance(i, float) for i in self.n_trees]).all():
            if 0 < max(self.n_trees) <= 1.0 and 0 < min(self.n_trees):
                # round to closest integer
                self.n_trees = (np.array(self.n_trees)*tot_estimators+0.5).astype(int)
                
    

    def is_fitted(self): #auxiliary function that returns boolean
        
        # if a pre-trained dict is passed, consider the model as fitted.
        # assume therefre that dicts only need to be formatted correctly
        if isinstance(self.clf, dict):
            self.clf = EnsembleWrapper(self.clf)
            return True
        else: #no dict, normal check if sklearn/sksurv model is fitted or not
            try:
                check_is_fitted(self.clf) #only with sklearn models (but works with all of them)
                return  True
            except: #check_is_fitted throws exception, we need it to output 'False'
                return False


    def fit(self, X, y): # works as inteded with sklearn models and trained models stored as compatible dictionaries
    
        # firstly fit if needed
        if self.force_refit == False and self.is_fitted():
            print("Model is already fitted, building explanation.")
        else:
            if self.verbose >= 0:
                print("Fitting the model...", end='')
            self.clf.fit(X, y, self.n_jobs)
            if self.verbose >= 0:
                print(" fitting complete")
                
        # then check whether the input grid values are admissible        
        self._validate_p_grid()
    
        if self.verbose >= 2:
            print("oracle_sample is: {}".format(self.ys_oracle))
            
            
        if self.set_up == "auto": # automatically determine scenario based on fitted classifier
            
            if (isinstance(self.clf, dict) or isinstance(self.clf, EnsembleWrapper)):
                raise ValueError("Dictionary format (wrapped ensemble) not compatible with \'auto\' set-up selection. Select manually")    
        
            elif isinstance(self.clf, sklearn.ensemble.RandomForestClassifier):
                if self.clf.n_outputs_ == 1:
                    self.set_up = 'binary'
                else:
                    self.set_up = 'multi-label'
            elif isinstance(self.clf, sklearn.ensemble.RandomForestRegressor):
                if y.ndims < 2 or self.clf.n_outputs_ == 1:
                    self.set_up = 'regression'
                else:
                    self.set_up = 'multi-target'
            elif isinstance(self.clf, sksurv.ensemble.forest.RandomSurvivalForest):
                if self.clf.n_outputs_ == self.clf.unique_times_.shape[0]:
                    self.set_up = 'survival'
                else:
                    self.set_up = 'multi-variate-sa'
                    raise ValueError('n_outputs_ shape != unique_times_ shape: {} != {}\n'
                                     'Note that multi-event Survival analysis is '
                                     'not supported yet'.format(self.clf.n_outputs_.shape,
                                                                self.clf.unique_times_.shape))
            else:
                raise ValueError("Provided model is not recognized or compatible with Bellatrex:", self.clf)
                
            if self.verbose > 0:
                print("Automatically set scenario to: ", self.set_up)
            
        return self
    
    
    def explain(self, X, idx, out_file=None): 
        
                
        sample = X[idx:idx+1]
        
        if self.ys_oracle != None:
            self.ys_oracle = self.ys_oracle.iloc[idx] #pick needed one

                        
        param_grid = {              #lists or single entries
            "n_trees": self.n_trees, 
            "n_dims" : self.n_dims,
            "n_clusters": self.n_clusters
            }
        
        for key in param_grid:
            if not isinstance(param_grid[key], (list, np.ndarray)):
                param_grid[key] = [param_grid[key]] # single entry to list
        
        
        grid_list = list(ParameterGrid(param_grid))
        best_perf = -np.inf
        
        
        ETrees = TreeExtraction(self.proj_method, self.dissim_method,
                                self.feature_represent,
                                # referred as (\tau, d, K) in the paper
                                self.n_trees, self.n_dims, self.n_clusters, 
                                self.pre_select_trees,
                                self.fidelity_measure,
                                self.clf,
                                self.ys_oracle,
                                self.set_up, sample, self.verbose
                                )
        
        
        ''' the TreeExtraction method does most of the computation,
        Hyperparamter optmimisation takes place here, where all possible 
        TreeExtraction( \tau, d, K)  candidates are compared and the hyperparameter
        combination with highest fidelity is selected.
        '''
        
        # setting default "best", params in case of error
        best_params = {"n_clusters": 2, "n_dims": 2, "n_trees": 20}
        

        if self.n_jobs == 1:
            for params in grid_list: #tuning here:
                try:
                    candidate = ETrees.set_params(**params).extract_trees()
                    perf = candidate.score(self.fidelity_measure, self.ys_oracle)
                except: # e.g. a ConvergeWarning from kmeans
                    print('Warning, something went wrong, skipping candidate:', params)
                    perf = -np.inf
                    
                if self.verbose >= 5:
                    print("params:", params)
                    print("fidelity current candidate: {:.4f}".format(perf))
    
                if perf > best_perf:
                    best_perf = perf
                    best_params = params

        elif self.n_jobs > 1:
            warnings.warn('Multiprocessing is not optimized, and the speed-up is marginal. \
Set n_jobs = 1 to avoid this warning')
            
            def missing_params_dict(given_params, class_instance):
                param_names = class_instance.__init__.__code__.co_varnames[1:]
                
                param_values = {name: getattr(class_instance, name) for name in param_names}
                
                missing_params = {key: value for key, value in param_values.items() if key not in given_params}
                return missing_params
            
            # Example usage
            provided_params = list(grid_list[0].keys())
            class_instance = ETrees
            
            constant_params = missing_params_dict(provided_params, class_instance)
            
            def create_ETrees_instance(constant_params, **params):
                return TreeExtraction(**constant_params, **params)

            # #function to be called in parallel processing:
            def run_candidate(create_instance_func, fidelity_measure, ys_oracle,
                              constant_params, **params):
                candidate = None
                try:
                    # print(f"Running with params: {params}")
                    etrees_instance = create_instance_func(constant_params, **params)  # Create a new instance using the provided function
                    candidate = etrees_instance.extract_trees()
                    perf = candidate.score(fidelity_measure, ys_oracle)
                    # print(f"Performance: {perf}")
                    
                except Exception as e:
                    warnings.warn(f'Warning, something went wrong ({str(e)}), skipping candidate: {params}')
                    perf = -np.inf
                return perf, params
        
            # Pass the required variables to run_candidate function
            # passing ETrees class, not instance
            results = Parallel(n_jobs=self.n_jobs, prefer="threads")(
                delayed(run_candidate)(create_ETrees_instance, self.fidelity_measure,
                                       self.ys_oracle, constant_params, **params) for params in grid_list)
            
            
            perfs, params_list = zip(*results)
            
            best_idx = np.argsort(perfs)[::-1][0]  # take top performing index
            best_perf = perfs[best_idx]
            best_params = params_list[best_idx]
            
        if best_perf == -np.inf: # if still the case, everything went wrong...
            warnings.warn("The GridSearch did not find any meaningful configuration, setting default parameters")
            

        # closed "GridSearch" loop, storing score of the best configuration
        #if best_perf > -np.inf: things should be alright
        tuned_method = ETrees.set_params(**best_params).extract_trees() # treeExtraction object
        #instant_method = tuned_method.extract_trees() # TreeExtraction object        
        sample_score = tuned_method.score(self.fidelity_measure, self.ys_oracle)
                        
        final_extract_trees = tuned_method.final_trees_idx
        final_cluster_sizes =  tuned_method.cluster_sizes
        
        ''' compute Bellatrex prediction  here as well. Useful for printing in the future '''
        
        if not isinstance(self.clf, RandomSurvivalForest): #shape must be (n,p) with n=1
            surrogate_pred = np.array([0.0]*self.clf.n_outputs_).reshape(sample.shape[0],-1)
        else:
            surrogate_pred = np.array([0.0]) #shape must be (1,)
        
        for tree_idx, cluster_size in zip(final_extract_trees, final_cluster_sizes):
            cluster_weight = cluster_size/np.sum(final_cluster_sizes)
            surrogate_pred += predict_helper(self.clf[tree_idx], sample.values)*cluster_weight 
          
                
        surrogate_pred_str = frmt_preds_to_print(surrogate_pred, digits_single=4)
        
        if self.verbose >= 1:
            print("best params:", best_params)
            print("Achieved fidelity: {:.4f}".format(best_perf))
            print("(Tuned according to {})".format(self.fidelity_measure))
            
        if self.verbose >= 2: #and sample_info.final_trees_idx > 1
            print("final trees indeces: {}".format(final_extract_trees))
            print("final cluster sizes: {}".format(final_cluster_sizes))

            # store rules in written file:
            if out_file not in [None, False]: # otherwise, do not create and store any file
                
                # Overwrite the file to start with an empty file
                with open(out_file, 'w+') as f:
                    pass #refreshes the file, closes file as well AFAIK
                
                with open(out_file, 'a+') as f:
                    for idx, cluster_size in zip(final_extract_trees, final_cluster_sizes):
                        rule_to_file(self.clf[idx],
                                     sample, X.columns,
                                     cluster_size/np.sum(final_cluster_sizes),
                                     self.MAX_FEATURE_PRINT, f)
                                            
                    f.write('Bellatrex prediction: {}'.format(surrogate_pred_str))
                    f.close()
                        
                with open(out_file.split('.')[0]+"-extra.txt", 'w+') as f:
                    pass #refreshes the file, closes file as well AFAIK
                    
                with open(out_file.split('.')[0]+"-extra.txt", 'a+') as f:
                    for idx in range(self.clf.n_estimators):
                        if idx not in final_extract_trees: #non selected-trees (extra info, plotting their paths in background for comparison)
                            rule_to_file(self.clf[idx],
                                         sample, X.columns, -1, #setting ruleweight to -1 (invalid number), or to 0 is also fine
                                         self.MAX_FEATURE_PRINT, f)                     
                    f.close()
        
        if self.verbose >= 3:

            plot_kmeans, plot_data_bunch = tuned_method.preselect_represent_cluster_trees()
            
            plot_preselected_trees(plot_data_bunch, plot_kmeans,
                                   tuned_method, final_extract_trees,
                                   base_font_size=self.FONT_SIZE,
                                   plot_dpi=self.dpi_figure,
                                   colormap=self.colormap)
            if self.show:
                plt.show()

                

        if self.verbose >= 4.0 and self.plot_GUI  == False:
            
            for tree_idx, cluster_size in zip(final_extract_trees, final_cluster_sizes):
                #if X.shape[1] < 10: # or improve rule_print_inline function!
                rule_print_inline(self.clf[tree_idx], sample,
                                      weight=cluster_size/np.sum(final_cluster_sizes),
                                      max_features_print=self.MAX_FEATURE_PRINT)
        print('Bellatrex prediction:', surrogate_pred_str)
        
        # if hasattr(self.clf, 'predict_proba'):
        #     y_pred_orig = self.clf.predict_proba(sample)[:,1]
        # else:
        #     y_pred_orig = self.clf.predict(sample)
        y_pred_orig = predict_helper(self.clf, sample)
            
        print('Black box prediction: ' + frmt_preds_to_print(y_pred_orig, digits_single=4))
        print('#'*54, flush=True)
        

        if self.verbose >= 4.0 and self.plot_GUI == True:
            
            from code_scripts.GUI_plots_code import plot_with_interface
            plot_with_interface(plot_data_bunch, plot_kmeans,
                                input_method=tuned_method,
                                max_depth=self.plot_max_depth)
            
            try:
                os.remove("colourbar0.png")
                os.remove("colourbar1.png")
            except:
                warnings.warn("\'colourbar*.png\' file not found")
        
        '''     return format:
            - tuned_method.score(self.fidelity_measure) is a float
            - tuned_method.local_prediction() is an array of shape (#labels,)
                - REMARK: single output local_prediction() is [float]
            - tuned_method is a TreeExtraction object with extra info:
                -(clf, cluster info, optimal hyperparams, set_up, 
                  projection method, dissim. measure...)
        '''
        
        
        tuned_method.sample_score = sample_score

        return tuned_method.local_prediction(), tuned_method # or return just .local_prediction ?
        #sample_score
            
        ###	improve output with
        '''
			Bunch(fidelity=str(self.fidelity_measure),
				score=tuned_method.score(self.fidelity_measure),
				local_pred=tuned_method.local_prediction(),
				n_trees= etc etc)
        '''
        
    def predict_survival_curve(self, X, idx):
        if not self.set_up in ["surv", "survival"]:
            raise ValueError("Input set-up is not a time-to-event!")
        return ValueError("Not implemented yet")
    
    def predict_median_surv_time(self, X, idx):
        if not self.set_up in ["surv", "survival"]:
            raise ValueError("Input set-up is not a time-to-event!")
        return ValueError("Not implemented yet")


        